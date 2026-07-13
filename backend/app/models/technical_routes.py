"""定义基于已保存关键词事实的保守技术路线响应契约。"""

from pydantic import BaseModel, Field  # 提供路线字段和集合规模校验。


class TechnicalRoute(BaseModel):
    """表示由共享关键词形成的可审计技术路线。"""

    route_id: str  # 由规范化关键词生成的稳定路线标识。
    name: str  # 直接展示原始关键词，不推断方法学名称。
    summary: str  # 说明路线仅由关键词事实聚合得到。
    paper_ids: list[str] = Field(min_length=1)  # 绑定路线到已保存论文。
    representative_paper_ids: list[str] = Field(min_length=1, max_length=3)  # 保留按请求顺序的代表论文。
    evidence: list[str] = Field(min_length=1)  # 回显实际共享或单篇关键词证据。


class TechnicalRoutesResponse(BaseModel):
    """返回当前搜索结果内可验证的关键词路线集合。"""

    routes: list[TechnicalRoute] = Field(default_factory=list)  # 没有关键词时允许返回空路线集合。
