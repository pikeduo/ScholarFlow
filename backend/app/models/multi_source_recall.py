"""定义多源召回协调阶段输出的论文、网页发现与来源统计契约。"""

from pydantic import BaseModel, Field  # 提供多源协调结果的结构化校验。

from backend.app.models.discovery import SupplementalDiscoveryItem  # 保存不可合并的补充网页发现结果。
from backend.app.models.paper import PaperRecord  # 保存可进入后续规范化与去重阶段的论文记录。
from backend.app.models.source_routing import SourceRoutePlan  # 关联当前召回使用的可审计来源计划。


class MultiSourceRecallResult(BaseModel):
    """保存一次多源召回的分流结果、来源统计和安全降级信息。

    属性：
        route_plan：本次执行前生成的来源选择计划。
        papers：尚未跨源去重的统一论文记录。
        discoveries：不能合并为论文的补充网页发现项。
        source_counts：每个已选来源成功返回的条目数量。
        source_errors：来源调用失败或未注册时的安全错误摘要。
    """

    route_plan: SourceRoutePlan  # 保留实际执行的来源选择与预先降级说明。
    papers: list[PaperRecord] = Field(default_factory=list)  # 保存按路由来源顺序拼接的未去重论文记录。
    discoveries: list[SupplementalDiscoveryItem] = Field(default_factory=list)  # 保存独立于论文集合的网页发现结果。
    source_counts: dict[str, int] = Field(default_factory=dict)  # 保存每个来源的成功结果数。
    source_errors: dict[str, str] = Field(default_factory=dict)  # 保存不含密钥、路径和响应正文的来源错误摘要。
