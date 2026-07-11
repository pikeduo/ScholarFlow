"""提供 OpenAlex 单数据源检索的版本化 HTTP 接口。"""

from typing import Annotated  # 为 FastAPI 依赖注入声明清晰的参数类型。

from fastapi import APIRouter, Depends, HTTPException, status  # 声明路由、依赖和稳定 HTTP 错误。

from backend.app.adapters.arxiv import ArxivClient  # 装配 AI/计算机领域可选的预印本搜索适配器。
from backend.app.adapters.dblp import DblpClient  # 装配 AI/计算机领域可选的书目搜索适配器。
from backend.app.adapters.openalex import OpenAlexClient, OpenAlexClientError  # 使用已封装的外部客户端和已净化异常。
from backend.app.adapters.semantic_scholar import SemanticScholarClient  # 装配已启用时可路由的核心语义来源适配器。
from backend.app.adapters.tavily import TavilyClient  # 装配仅用于独立网页补充发现的适配器。
from backend.app.core.logging import logger  # 记录服务不可用时的完整错误堆栈。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 声明多源检索的稳定融合响应模型。
from backend.app.models.query import QuerySchema  # 接收 FastAPI 自动校验的结构化检索请求。
from backend.app.models.query_intent import QueryIntent  # 接收已规划完成的多源检索意图。
from backend.app.models.search import SearchResult  # 声明稳定的成功响应模型。
from backend.app.services.multi_source_recall import MultiSourceRecallCoordinator  # 执行动态路由、并发召回和跨来源融合。
from backend.app.services.openalex_search import OpenAlexSearchService  # 复用客户端与去重的业务编排服务。
from backend.app.services.source_router import SourceRouter  # 使用确定性规则选择本次可调用的数据源。


router = APIRouter(prefix="/search")  # 将检索接口归入固定资源路径。


def get_openalex_search_service() -> OpenAlexSearchService:
    """构造生产环境使用的 OpenAlex 检索服务。

    返回：
        OpenAlexSearchService：使用真实 OpenAlex 适配器的服务实例。
    """
    return OpenAlexSearchService(OpenAlexClient())  # 将 HTTP、鉴权和响应解析保持在适配层内。


def get_multi_source_recall_coordinator() -> MultiSourceRecallCoordinator:
    """构造生产环境使用的多源召回、融合和网页补充协调器。

    返回：
        MultiSourceRecallCoordinator：使用真实适配器但按路由规则按需调用来源的协调器。
    """
    return MultiSourceRecallCoordinator(  # 将适配器装配集中在 API 依赖层，避免服务层绑定具体供应商。
        source_router=SourceRouter(),  # 使用集中配置驱动的确定性来源选择规则。
        academic_adapters={  # 注册所有已实现的学术来源；路由器决定本次是否实际调用。
            "openalex": OpenAlexClient(),  # 注册固定主学术来源。
            "semantic_scholar": SemanticScholarClient(),  # 注册已启用时可参与核心召回的语义来源。
            "arxiv": ArxivClient(),  # 注册 AI/计算机领域按需使用的预印本来源。
            "dblp": DblpClient(),  # 注册 AI/计算机领域按需使用的书目来源。
        },
        web_discovery_adapters={"tavily": TavilyClient()},  # 注册独立网页补充来源且永不进入论文融合。
    )


@router.post("/openalex", response_model=SearchResult, status_code=status.HTTP_200_OK, summary="检索 OpenAlex 论文")
async def search_openalex(
    query: QuerySchema,
    service: Annotated[OpenAlexSearchService, Depends(get_openalex_search_service)],
) -> SearchResult:
    """按结构化查询检索 OpenAlex 并返回去重后的论文列表。

    参数：
        query：由 FastAPI 校验的结构化检索约束。
        service：可由测试或未来工作流替换的 OpenAlex 搜索服务。
    返回：
        SearchResult：包含论文和检索阶段统计的稳定响应。
    异常：
        HTTPException：OpenAlex 服务不可用时返回不含内部细节的 503 响应。
    """
    try:  # 将已净化的外部服务异常转换为稳定 HTTP 边界。
        return await service.search(query)  # 调用服务层完成召回、去重和统计。
    except OpenAlexClientError:  # 适配层已处理网络、响应和密钥配置错误。
        logger.exception("OpenAlex 搜索接口调用失败")  # 保留完整堆栈供运维排查，不记录请求正文。
        raise HTTPException(  # 返回不含密钥、路径或原始响应的公共错误。
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAlex 搜索服务暂时不可用，请稍后重试",
        ) from None


@router.post("/multi-source", response_model=MultiSourceRecallResult, status_code=status.HTTP_200_OK, summary="按意图检索并融合多源论文")
async def search_multi_source(
    query: QueryIntent,
    coordinator: Annotated[MultiSourceRecallCoordinator, Depends(get_multi_source_recall_coordinator)],
) -> MultiSourceRecallResult:
    """按 QueryIntent 完成多源召回、分层排序与核验，并返回独立网页补充发现。

    参数：
        query：由调用方或 Query Agent 提供的完整、已校验检索意图。
        coordinator：可由测试替换的多源召回与融合协调器。
    返回：
        MultiSourceRecallResult：包含最终证据化论文、来源数量、排序统计、降级信息和独立网页发现。
    异常：
        HTTPException：协调器出现未预期内部故障时返回不泄露细节的 503 响应。
    """
    try:  # 协调器已隔离单源错误，此处仅处理无法形成稳定响应的未预期故障。
        return await coordinator.recall(query)  # 执行动态路由、并发召回、融合与安全降级。
    except Exception:  # 不向前端暴露融合实现、适配器装配或配置细节。
        logger.exception("多源检索接口调用失败")  # 在受控日志中保留完整堆栈供运维排查。
        raise HTTPException(  # 返回稳定且可理解的服务不可用响应。
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="多源论文检索服务暂时不可用，请稍后重试",
        ) from None
