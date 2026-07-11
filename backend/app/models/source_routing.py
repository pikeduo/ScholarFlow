"""定义动态来源路由节点输出的可审计计划契约。"""

from typing import Literal  # 限制首版路由器可以主动选择的学术来源范围。

from pydantic import BaseModel, Field  # 提供来源路由计划的结构化校验。

from backend.app.models.discovery import DiscoverySource  # 引用补充网页发现来源类型。


RoutableAcademicSource = Literal["openalex", "arxiv", "dblp", "semantic_scholar"]  # 声明当前路由器可主动选择的学术来源。


class SourceRoutePlan(BaseModel):
    """保存一次查询的来源选择、降级原因与补充发现边界。

    属性：
        academic_sources：可进入论文召回和后续融合的学术来源。
        web_discovery_sources：仅返回不可合并网页发现项的补充来源。
        selection_reasons：每个已选择来源的可展示、无敏感信息理由。
        unavailable_reasons：本应可选但因配置或策略未启用的来源说明。
    """

    academic_sources: list[RoutableAcademicSource] = Field(min_length=1)  # 至少保留 OpenAlex 主源以避免空检索计划。
    web_discovery_sources: list[DiscoverySource] = Field(default_factory=list)  # 补充网页来源与论文来源严格分离。
    selection_reasons: dict[str, str] = Field(default_factory=dict)  # 保存每个已选来源的非敏感决策理由。
    unavailable_reasons: dict[str, str] = Field(default_factory=dict)  # 保存未选来源的非敏感降级或配置原因。
