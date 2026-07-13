"""提供只基于 SQLite 已保存事实的论文比较接口。"""

from typing import Annotated  # 为 FastAPI 依赖注入声明清晰类型。

from fastapi import APIRouter, Depends, HTTPException, status  # 声明比较路由和稳定公共错误。

from backend.app.api.routes.search import get_search_run_state_store  # 复用搜索结果快照存储装配。
from backend.app.core.logging import logger  # 记录不含论文正文的存储边界异常。
from backend.app.models.comparison import ComparePapersRequest, ComparePapersResponse  # 声明稳定请求和响应契约。
from backend.app.services.paper_comparison import PaperComparisonService  # 保持事实投影与 HTTP 层解耦。
from backend.app.services.search_run_store import SearchRunStateStore, SearchRunStoreError  # 隔离 SQLite 读取与错误映射。


router = APIRouter(prefix="/compare")  # 将论文比较组织到固定版本化资源路径。


@router.post("", response_model=ComparePapersResponse, status_code=status.HTTP_200_OK, summary="比较已保存论文")
def compare_papers(
    request: ComparePapersRequest,
    state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)],
) -> ComparePapersResponse:
    """比较两至五篇已保存论文的元数据与核验证据，不调用外部服务。

    参数：
        request：包含已校验数量和唯一性的内部论文标识。
        state_store：可替换的 SQLite 搜索结果快照读取适配层。
    返回：
        ComparePapersResponse：按请求顺序排列的事实型对比结果。
    异常：
        HTTPException：论文缺失时返回 404，存储故障时返回 503。
    """
    try:  # 将 SQLite 与历史快照解析错误隔离为公共服务边界。
        papers = state_store.get_papers(request.paper_ids)  # 批量读取已保存最终结果，绝不调用来源或 PDF。
    except SearchRunStoreError:  # 不泄露 SQLite 路径、SQL 或快照正文。
        logger.exception("论文比较读取接口失败：数量=%s", len(request.paper_ids))  # 只记录安全数量与完整堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="论文比较数据暂时不可用，请稍后重试") from None  # 返回可重试的公共提示。
    papers_by_id = {paper.paper_id: paper for paper in papers}  # 消除存储实现可能返回的重复记录，并建立按标识重排的索引。
    if len(papers_by_id) != len(request.paper_ids) or any(paper_id not in papers_by_id for paper_id in request.paper_ids):  # 任何缺失论文都不能形成可信固定列对比。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="存在未保存的论文，无法比较")  # 不暴露哪些快照或论文实际存在。
    ordered_papers = [papers_by_id[paper_id] for paper_id in request.paper_ids]  # 将固定列顺序收敛到 API 契约，而不是依赖仓储或测试替身的返回顺序。
    return PaperComparisonService().compare(ordered_papers)  # 仅投影已保存事实和核验证据，不生成额外推断。
