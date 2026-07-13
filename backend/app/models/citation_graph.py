"""定义仅基于已保存论文事实的受限引用图响应契约。"""

from typing import Literal  # 限制图边类型为可审计的事实关系。

from pydantic import BaseModel, Field  # 提供图节点与边的稳定响应校验。


GraphEdgeType = Literal["cites", "same_work"]  # 当前仅暴露保存引用和版本族事实，不伪造被引或语义关系。


class CitationGraphNode(BaseModel):
    """表示搜索结果集合中的单篇可展示论文节点。"""

    paper_id: str  # 绑定节点到内部稳定论文标识。
    title: str  # 显示可截断的论文标题。
    year: int | None = None  # 展示来源提供的发表年份。
    relevance: float | None = None  # 复用既有排序分数，不重新计算语义关系。
    source: str  # 展示主溯源来源。


class CitationGraphEdge(BaseModel):
    """表示两个已保存节点之间可核验的关系边。"""

    source_paper_id: str  # 边起点论文标识。
    target_paper_id: str  # 边终点论文标识。
    edge_type: GraphEdgeType  # 标记引用或同一版本族关系。


class CitationGraphResponse(BaseModel):
    """返回受节点上限保护且不含外部扩展的搜索结果引用图。"""

    nodes: list[CitationGraphNode] = Field(max_length=50)  # 限制图节点数避免前端渲染卡顿。
    edges: list[CitationGraphEdge] = Field(default_factory=list)  # 仅返回已有事实可支持的关系。
    max_nodes: int = Field(ge=1, le=50)  # 回显实际采用的节点上限供前端说明裁剪边界。
    truncated: bool = False  # 标记请求集合是否因节点上限被裁剪。
