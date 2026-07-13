"""提供只基于 SQLite 已保存关键词的保守技术路线接口。"""

from typing import Annotated  # 声明 FastAPI 依赖与查询参数类型。

from fastapi import APIRouter, Depends, HTTPException, Query, status  # 声明路线读取接口与安全错误边界。

from backend.app.api.routes.search import get_search_run_state_store  # 复用搜索结果 SQLite 存储装配。
from backend.app.core.logging import logger  # 记录存储异常而不输出论文内容。
from backend.app.models.technical_routes import TechnicalRoutesResponse  # 声明稳定路线响应契约。
from backend.app.services.search_run_store import SearchRunStateStore, SearchRunStoreError  # 隔离持久化读取边界。
from backend.app.services.technical_routes import TechnicalRouteService  # 构建关键词事实路线。


router = APIRouter(prefix="/routes")  # 将技术路线资源组织到固定版本化路径。


@router.get("", response_model=TechnicalRoutesResponse, status_code=status.HTTP_200_OK, summary="读取已保存搜索结果的技术路线")
def get_technical_routes(paper_ids: Annotated[list[str], Query(min_length=1, max_length=50)], state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)]) -> TechnicalRoutesResponse:
    """按当前论文集合的来源关键词生成保守路线，不调用模型或外部来源。"""
    normalized_ids = [paper_id.strip() for paper_id in paper_ids]  # 规范化查询中的内部论文标识。
    if any(not paper_id for paper_id in normalized_ids) or len(set(normalized_ids)) != len(normalized_ids):  # 阻止空标识或重复节点进入路线读取。
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="技术路线论文标识不能为空且不能重复")  # 保持公共输入错误边界。
    try:  # 将 SQLite 和快照解析错误映射为服务错误。
        papers = state_store.get_papers(normalized_ids)  # 仅读取已保存最终结果，不调用来源或模型。
    except SearchRunStoreError:  # 不泄露底层存储细节。
        logger.exception("技术路线读取接口失败：数量=%s", len(normalized_ids))  # 仅记录请求数量和完整堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="技术路线数据暂时不可用，请稍后重试") from None  # 返回可重试公共提示。
    papers_by_id = {paper.paper_id: paper for paper in papers}  # 建立稳定重排索引。
    if len(papers_by_id) != len(normalized_ids) or any(paper_id not in papers_by_id for paper_id in normalized_ids):  # 不允许缺失论文产生部分且误导的路线。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="存在未保存的论文，无法生成技术路线")  # 返回安全资源错误。
    return TechnicalRouteService().build([papers_by_id[paper_id] for paper_id in normalized_ids])  # 按用户结果顺序构建关键词事实路线。
