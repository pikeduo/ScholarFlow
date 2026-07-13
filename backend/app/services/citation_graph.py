"""从 SQLite 已保存论文事实构建受限引用与版本族关系图。"""

from collections.abc import Sequence  # 标注按用户选择顺序恢复的论文集合。

from backend.app.models.citation_graph import CitationGraphEdge, CitationGraphNode, CitationGraphResponse, GraphEdgeType  # 使用稳定图响应契约。
from backend.app.models.paper import PaperRecord  # 读取引用和版本族等已保存论文事实。


class CitationGraphService:
    """构建不调用外部来源、PDF 或模型的轻量搜索结果关系图。"""

    def build(self, papers: Sequence[PaperRecord], *, max_nodes: int, edge_types: set[GraphEdgeType]) -> CitationGraphResponse:
        """从已保存论文集合生成受限节点、引用边和版本族边。

        参数：
            papers：按用户请求顺序恢复的规范化论文集合。
            max_nodes：前端允许展示的最大节点数。
            edge_types：用户允许保留的事实边类型集合。
        返回：
            CitationGraphResponse：只包含集合内部可验证关系的图数据。
        """
        visible_papers = list(papers[:max_nodes])  # 仅取请求顺序靠前的节点，确保裁剪行为稳定可解释。
        visible_ids = {paper.paper_id for paper in visible_papers}  # 构造集合以过滤指向图外论文的引用。
        nodes = [CitationGraphNode(paper_id=paper.paper_id, title=paper.title, year=paper.year, relevance=paper.llm_relevance_score if paper.llm_relevance_score is not None else paper.cross_encoder_score, source=paper.source) for paper in visible_papers]  # 投影可展示元数据且不新增推断。
        edges: list[CitationGraphEdge] = []  # 累积经事实校验的图关系。
        seen_edges: set[tuple[str, str, GraphEdgeType]] = set()  # 避免同一关系重复渲染。
        if "cites" in edge_types:  # 仅在调用方允许时处理来源提供的引用事实。
            for paper in visible_papers:  # 逐篇读取其引用标识列表。
                for reference_id in paper.references:  # 只保留目标也在当前可展示集合内的边。
                    edge_key = (paper.paper_id, reference_id, "cites")  # 构造可去重的有向引用关系键。
                    if reference_id in visible_ids and reference_id != paper.paper_id and edge_key not in seen_edges:  # 排除图外、自环和重复关系。
                        edges.append(CitationGraphEdge(source_paper_id=paper.paper_id, target_paper_id=reference_id, edge_type="cites"))  # 保存可由来源引用字段支持的边。
                        seen_edges.add(edge_key)  # 标记该引用边已处理。
        if "same_work" in edge_types:  # 仅在调用方允许时连接已保存的版本族事实。
            family_members: dict[str, list[PaperRecord]] = {}  # 按非空版本族聚合可见论文。
            for paper in visible_papers:  # 只使用当前图内论文的版本族关系。
                if paper.work_family_id:  # 缺失版本族时不能推断同一工作关系。
                    family_members.setdefault(paper.work_family_id, []).append(paper)  # 收集同一事实族的成员。
            for members in family_members.values():  # 为每个版本族生成确定性无向代表边。
                for index, paper in enumerate(members):  # 从第二个成员开始连接到前一个成员。
                    if index == 0:  # 首个成员没有前驱节点。
                        continue  # 避免无意义边。
                    edge_key = (paper.paper_id, members[index - 1].paper_id, "same_work")  # 构造稳定版本族边键。
                    edges.append(CitationGraphEdge(source_paper_id=paper.paper_id, target_paper_id=members[index - 1].paper_id, edge_type="same_work"))  # 使用链式边避免同族大集合产生完全图。
                    seen_edges.add(edge_key)  # 保持去重集合完整以便后续演进。
        return CitationGraphResponse(nodes=nodes, edges=edges, max_nodes=max_nodes, truncated=len(papers) > len(visible_papers))  # 返回裁剪状态，前端可明确说明图并非外部全量引文网络。
