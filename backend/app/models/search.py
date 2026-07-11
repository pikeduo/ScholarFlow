"""定义检索服务返回的论文列表与阶段统计。"""

from pydantic import BaseModel, Field  # 提供稳定的服务输出数据模型。

from backend.app.models.paper import Paper  # 复用已规范化的论文领域模型。


class SearchResult(BaseModel):
    """描述单个学术数据源完成召回和去重后的检索结果。

    属性：
        papers：保持数据源召回顺序的去重和规则过滤后论文。
        recalled_count：客户端成功映射的原始论文数量。
        deduplicated_count：去重后可交给后续排序阶段的论文数量。
        filtered_count：本地规则过滤移除的论文数量。
    """

    papers: list[Paper] = Field(default_factory=list)  # 保存去重和规则过滤后的规范化论文列表。
    recalled_count: int = Field(ge=0)  # 记录数据源本轮成功召回的论文数。
    deduplicated_count: int = Field(ge=0)  # 记录去重后保留的论文数。
    filtered_count: int = Field(default=0, ge=0)  # 记录本地规则过滤移除的论文数。
