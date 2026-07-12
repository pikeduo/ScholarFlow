"""提供 OpenAlex 单数据源检索的版本化 HTTP 接口。"""

from functools import lru_cache  # 在服务进程内复用昂贵的本地模型和来源限流状态。
from typing import Annotated  # 为 FastAPI 依赖注入声明清晰的参数类型。

from fastapi import APIRouter, Depends, HTTPException, status  # 声明路由、依赖和稳定 HTTP 错误。

from backend.app.adapters.arxiv import ArxivClient  # 装配 AI/计算机领域可选的预印本搜索适配器。
from backend.app.adapters.dblp import DblpClient  # 装配 AI/计算机领域可选的书目搜索适配器。
from backend.app.adapters.openalex import OpenAlexClient, OpenAlexClientError  # 使用已封装的外部客户端和已净化异常。
from backend.app.adapters.semantic_scholar import SemanticScholarClient  # 装配已启用时可路由的核心语义来源适配器。
from backend.app.adapters.tavily import TavilyClient  # 装配仅用于独立网页补充发现的适配器。
from backend.app.core.logging import logger  # 记录服务不可用时的完整错误堆栈。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 声明多源检索的稳定融合响应模型。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 声明多轮搜索的稳定运行状态和最终结果响应模型。
from backend.app.models.natural_search import NaturalSearchRequest  # 接收前端自然语言问题和显式约束。
from backend.app.models.query import QuerySchema  # 接收 FastAPI 自动校验的结构化检索请求。
from backend.app.models.query_intent import QueryIntent  # 接收已规划完成的多源检索意图。
from backend.app.models.search import SearchResult  # 声明稳定的成功响应模型。
from backend.app.models.search_run import SearchRunState  # 声明可按运行标识读取的持久化状态响应。
from backend.app.services.multi_source_recall import MultiSourceRecallCoordinator  # 执行动态路由、并发召回和跨来源融合。
from backend.app.services.multi_round_search import MultiRoundSearchController  # 执行有限轮次的召回、缺口修复与保护性停止。
from backend.app.services.search_run_store import SearchRunStateStore, SqliteSearchRunStateStore, SearchRunStoreError  # 装配 SQLite 状态持久化并映射安全读取错误。
from backend.app.services.openalex_search import OpenAlexSearchService  # 复用客户端与去重的业务编排服务。
from backend.app.services.query_planning import QueryPlanningService  # 在多源检索前生成结构化英文查询计划。
from backend.app.adapters.deepseek_query_planner import QueryPlanningError  # 将查询规划故障转换为稳定 HTTP 错误。
from backend.app.services.source_router import SourceRouter  # 使用确定性规则选择本次可调用的数据源。


router = APIRouter(prefix="/search")  # 将检索接口归入固定资源路径。


def get_openalex_search_service() -> OpenAlexSearchService:
    """构造生产环境使用的 OpenAlex 检索服务。

    返回：
        OpenAlexSearchService：使用真实 OpenAlex 适配器的服务实例。
    """
    return OpenAlexSearchService(OpenAlexClient())  # 将 HTTP、鉴权和响应解析保持在适配层内。


@lru_cache(maxsize=1)
def get_multi_source_recall_coordinator() -> MultiSourceRecallCoordinator:
    """构造并在当前进程复用多源召回、融合、排序和网页补充协调器。

    返回：
        MultiSourceRecallCoordinator：复用真实适配器、限流状态及本地模型实例的协调器。
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


@lru_cache(maxsize=1)
def get_multi_round_search_controller() -> MultiRoundSearchController:
    """构造并复用多轮搜索控制器，复用单轮协调器的来源限流和模型实例。"""
    return MultiRoundSearchController(get_multi_source_recall_coordinator(), state_store=get_search_run_state_store())  # 让多轮执行复用来源模型和运行状态持久化适配层。


@lru_cache(maxsize=1)
def get_search_run_state_store() -> SearchRunStateStore:
    """构造并复用 SQLite 搜索运行状态存储适配层。"""
    return SqliteSearchRunStateStore()  # 每次存取内部创建短生命周期会话，适合进程级控制器复用。


@lru_cache(maxsize=1)
def get_query_planning_service() -> QueryPlanningService:
    """构造并复用自然语言查询规划服务。"""
    return QueryPlanningService()  # 复用 DeepSeek 配置且不在构造时发起请求。


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


@router.post("/multi-round", response_model=MultiRoundSearchResult, status_code=status.HTTP_200_OK, summary="按意图执行有限轮次的多源论文检索")
async def search_multi_round(
    query: QueryIntent,
    controller: Annotated[MultiRoundSearchController, Depends(get_multi_round_search_controller)],
) -> MultiRoundSearchResult:
    """直接执行用户已编辑的 QueryIntent，并返回多轮过程的最终状态与停止原因。

    参数：
        query：无需再次调用 Query Agent 的完整、已校验查询意图。
        controller：可在测试中替换的多轮搜索控制器。
    返回：
        MultiRoundSearchResult：累计论文、覆盖报告、运行状态与安全来源统计。
    异常：
        HTTPException：控制器未能形成稳定结果时返回不泄露内部实现的 503 响应。
    """
    try:  # 控制器会隔离来源错误，此处仅转换未预期的服务边界故障。
        return await controller.run(query)  # 直接使用编辑后的意图，避免重复调用 Query Agent。
    except Exception:  # 不向调用方暴露模型装配、来源实现或工作流内部细节。
        logger.exception("多轮多源检索接口调用失败")  # 在受控日志保留完整堆栈供运维排查。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="多轮论文检索服务暂时不可用，请稍后重试") from None  # 返回安全、可理解且稳定的公共错误。


@router.get("/runs/{run_id}", response_model=SearchRunState, status_code=status.HTTP_200_OK, summary="读取可恢复的搜索运行状态")
def get_search_run_state(
    run_id: str,
    state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)],
) -> SearchRunState:
    """按 run_id 读取最新轻量运行快照，供轮询、SSE 补偿和恢复入口使用。

    参数：
        run_id：多轮搜索响应中返回的稳定运行标识。
        state_store：可替换的搜索运行状态存储适配层。
    返回：
        SearchRunState：不重复包含完整论文集合的最新可恢复状态。
    异常：
        HTTPException：运行不存在时返回 404，存储不可用时返回安全 503。
    """
    try:  # 将存储访问故障隔离为稳定 HTTP 边界。
        state = state_store.get(run_id)  # 读取最近一次轮次或终态的轻量快照。
    except SearchRunStoreError:  # 不向前端暴露 SQLite 路径、SQL 或状态 JSON。
        logger.exception("搜索运行状态读取接口失败：运行=%s", run_id)  # 记录安全运行标识和完整堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="搜索运行状态暂时不可用，请稍后重试") from None  # 返回安全公共错误。
    if state is None:  # 不存在的运行标识属于稳定资源边界。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="搜索运行不存在")  # 避免将不存在误报为服务故障。
    return state  # 返回轻量状态，前端可据此补偿进度显示。


@router.post("/natural", response_model=MultiSourceRecallResult, status_code=status.HTTP_200_OK, summary="按自然语言检索多源论文")
async def search_natural(
    request: NaturalSearchRequest,
    planner: Annotated[QueryPlanningService, Depends(get_query_planning_service)],
    coordinator: Annotated[MultiSourceRecallCoordinator, Depends(get_multi_source_recall_coordinator)],
) -> MultiSourceRecallResult:
    """先解析自然语言查询，再执行现有多源召回和分层排序链路。"""
    try:  # 查询规划失败时拒绝退回整句低质量搜索。
        planning_result = await planner.plan(request)  # 生成英文检索式、结构化约束和可观测调用统计。
    except QueryPlanningError:  # 适配层已净化密钥、URL 和响应正文。
        logger.exception("自然语言查询规划失败")  # 在受控日志保留完整堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="查询理解服务暂时不可用，请稍后重试") from None  # 返回稳定错误。
    try:  # 复用已具备单源降级的多源协调器。
        recall_result = await coordinator.recall(planning_result.query_intent)  # 执行结构化检索计划。
        return recall_result.model_copy(update={  # 将 Query Agent 统计附加到自然入口响应，直接意图重搜保持零值。
            "query_planning_model_name": planning_result.model_name,  # 回显实际规划模型名称。
            "query_planning_prompt_tokens": planning_result.prompt_tokens,  # 回显本次规划输入 Token。
            "query_planning_completion_tokens": planning_result.completion_tokens,  # 回显本次规划输出 Token。
            "query_planning_duration_ms": planning_result.duration_ms,  # 回显本次规划耗时。
        })
    except Exception:  # 隔离无法形成稳定响应的未预期错误。
        logger.exception("自然语言多源检索失败")  # 记录完整堆栈且不输出查询正文。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="多源论文检索服务暂时不可用，请稍后重试") from None  # 返回稳定错误。


@router.post("/natural-multi-round", response_model=MultiRoundSearchResult, status_code=status.HTTP_200_OK, summary="按自然语言执行有限轮次的多源论文检索")
async def search_natural_multi_round(
    request: NaturalSearchRequest,
    planner: Annotated[QueryPlanningService, Depends(get_query_planning_service)],
    controller: Annotated[MultiRoundSearchController, Depends(get_multi_round_search_controller)],
) -> MultiRoundSearchResult:
    """先生成 QueryIntent，再执行多轮搜索并附加规划用量，供前端展示完整过程。

    参数：
        request：自然语言问题和用户显式检索条件。
        planner：可替换的自然语言 Query Agent 服务。
        controller：可替换的多轮搜索控制器。
    返回：
        MultiRoundSearchResult：包含执行意图、累计论文、停止原因和 Query Agent 统计。
    异常：
        HTTPException：规划或控制器不可用时返回稳定且不泄露内部细节的 503 响应。
    """
    try:  # 查询规划失败时禁止退回整句低质量检索。
        planning_result = await planner.plan(request)  # 先获得可编辑且可审计的结构化英文检索意图。
    except QueryPlanningError:  # 适配层已净化密钥、URL 与原始响应正文。
        logger.exception("多轮自然语言查询规划失败")  # 保留受控堆栈供服务排查。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="查询理解服务暂时不可用，请稍后重试") from None  # 返回安全公共错误。
    try:  # 多轮控制器使用规划意图执行实际召回、排序和缺口修复。
        result = await controller.run(planning_result.query_intent)  # 不将原始自然语言整句直接传递给来源适配器。
    except Exception:  # 隔离控制器装配或未预期内部错误。
        logger.exception("多轮自然语言检索接口调用失败")  # 仅记录受控堆栈，不记录完整原始查询。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="多轮论文检索服务暂时不可用，请稍后重试") from None  # 返回稳定服务不可用响应。
    updated_state = result.run_state.model_copy(update={"token_usage": result.run_state.token_usage + planning_result.prompt_tokens + planning_result.completion_tokens})  # 将 Query Agent 用量纳入运行总 Token 统计。
    return result.model_copy(  # 保持直接意图重搜零规划开销，同时自然入口回显规划统计。
        update={
            "run_state": updated_state,
            "query_intent": planning_result.query_intent,
            "query_planning_model_name": planning_result.model_name,
            "query_planning_prompt_tokens": planning_result.prompt_tokens,
            "query_planning_completion_tokens": planning_result.completion_tokens,
            "query_planning_duration_ms": planning_result.duration_ms,
        }
    )
