"""提供 OpenAlex 单数据源检索的版本化 HTTP 接口。"""

from typing import Annotated  # 为 FastAPI 依赖注入声明清晰的参数类型。

from fastapi import APIRouter, Depends, HTTPException, status  # 声明路由、依赖和稳定 HTTP 错误。

from backend.app.adapters.openalex import OpenAlexClient, OpenAlexClientError  # 使用已封装的外部客户端和已净化异常。
from backend.app.core.logging import logger  # 记录服务不可用时的完整错误堆栈。
from backend.app.models.query import QuerySchema  # 接收 FastAPI 自动校验的结构化检索请求。
from backend.app.models.search import SearchResult  # 声明稳定的成功响应模型。
from backend.app.services.openalex_search import OpenAlexSearchService  # 复用客户端与去重的业务编排服务。


router = APIRouter(prefix="/search")  # 将检索接口归入固定资源路径。


def get_openalex_search_service() -> OpenAlexSearchService:
    """构造生产环境使用的 OpenAlex 检索服务。

    返回：
        OpenAlexSearchService：使用真实 OpenAlex 适配器的服务实例。
    """
    return OpenAlexSearchService(OpenAlexClient())  # 将 HTTP、鉴权和响应解析保持在适配层内。


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
