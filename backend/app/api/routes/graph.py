"""提供仅基于 SQLite 已保存搜索结果的受限引用图接口。"""

from typing import Annotated, Literal  # 标注 FastAPI 查询参数和可选事实边类型。

from fastapi import APIRouter, Depends, HTTPException, Query, status  # 声明图路由、依赖和稳定错误边界。

from backend.app.api.routes.search import get_search_run_state_store  # 复用已保存搜索结果的 SQLite 存储装配。
from backend.app.core.logging import logger  # 记录存储边界异常但不输出论文内容。
from backend.app.models.citation_graph import CitationGraphResponse, GraphEdgeType  # 声明受限图响应契约。
from backend.app.services.citation_graph import CitationGraphService  # 构建纯事实型图数据。
from backend.app.services.search_run_store import SearchRunStateStore, SearchRunStoreError  # 隔离持久化读取并映射安全错误。


router = APIRouter(prefix="/graph")  # 将图谱资源组织在稳定版本化路径下。


@router.get("/citations", response_model=CitationGraphResponse, status_code=status.HTTP_200_OK, summary="读取已保存搜索结果的引用图")
def get_citation_graph(
    paper_ids: Annotated[list[str], Query(min_length=1, max_length=50)],
    state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)],
    max_nodes: Annotated[int, Query(ge=1, le=50)] = 30,
    edge_types: Annotated[list[Literal["cites", "same_work"]] | None, Query()] = None,
) -> CitationGraphResponse:
    """读取当前小集合中已有引用和版本族事实，不扩展外部引文网络。

    参数：
        paper_ids：需要进入图谱的已保存内部论文标识。
        state_store：可替换的 SQLite 搜索结果读取适配层。
        max_nodes：最多展示的节点数，默认 30。
        edge_types：可选的事实边类型；省略时展示引用和版本族边。
    返回：
        CitationGraphResponse：受节点上限保护的图节点、边和裁剪状态。
    异常：
        HTTPException：未知论文返回 404，存储故障返回 503。
    """
    normalized_ids = [paper_id.strip() for paper_id in paper_ids]  # 规范化重复查询参数中的资源标识。
    if any(not paper_id for paper_id in normalized_ids) or len(set(normalized_ids)) != len(normalized_ids):  # 空值或重复值会破坏图节点稳定性。
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="图谱论文标识不能为空且不能重复")  # 在读取 SQLite 前返回明确输入边界。
    try:  # 将 SQLite 和历史快照解析故障隔离为公共服务错误。
        papers = state_store.get_papers(normalized_ids)  # 仅读取已保存最终结果，不调用来源或 PDF。
    except SearchRunStoreError:  # 不泄露 SQL、路径或快照正文。
        logger.exception("引用图读取接口失败：数量=%s", len(normalized_ids))  # 仅记录节点请求数量与完整堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="引用图数据暂时不可用，请稍后重试") from None  # 返回可重试的公共提示。
    papers_by_id = {paper.paper_id: paper for paper in papers}  # 建立按请求顺序重排的稳定索引。
    if len(papers_by_id) != len(normalized_ids) or any(paper_id not in papers_by_id for paper_id in normalized_ids):  # 不允许部分事实集合伪装为完整图。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="存在未保存的论文，无法生成引用图")  # 不暴露哪些论文实际存在。
    selected_edge_types: set[GraphEdgeType] = set(edge_types or ["cites", "same_work"])  # 省略时只启用两类已有事实边。
    ordered_papers = [papers_by_id[paper_id] for paper_id in normalized_ids]  # 固定节点顺序以支持前端稳定布局。
    return CitationGraphService().build(ordered_papers, max_nodes=max_nodes, edge_types=selected_edge_types)  # 构建受限内部关系图且不进行外部扩展。
