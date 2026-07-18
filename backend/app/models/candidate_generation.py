"""定义规则过滤后、任何语义或 LLM 排序前的候选生成结果契约。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator  # 提供严格内部契约与阶段数量校验。

from backend.app.models.discovery import SupplementalDiscoveryItem  # 保持网页发现与学术论文候选分离。
from backend.app.models.paper import PaperRecord  # 保存已经规范化、融合和规则过滤的论文候选。
from backend.app.models.query_intent import QueryIntent  # 冻结本轮实际执行的结构化查询意图。
from backend.app.models.source_routing import SourceRoutePlan  # 保存发起来源调用前生成的可审计路由计划。


class CandidateGenerationResult(BaseModel):
    """保存来源调用至规则过滤阶段的输出，不包含任何本地模型或 LLM 排序结果。

    属性：
        route_plan：本轮真实执行的学术来源与独立网页发现来源计划。
        query_intent：本轮来源适配器和规则过滤实际消费的查询意图。
        papers：规则过滤后、BGE-M3 前的学术论文候选集合。
        discoveries：永不进入论文融合或排序的补充网页发现项。
        academic_source_counts：各学术来源成功映射为 PaperRecord 的数量。
        web_discovery_source_counts：各网页来源成功返回补充发现项的数量。
        academic_source_errors：学术来源的安全降级摘要。
        web_discovery_source_errors：网页来源的安全降级摘要。
        cache_hit_count：本轮来源响应缓存的有效命中数量。
        normalized_candidate_count：进入身份融合前的统一 PaperRecord 数量。
        deduplicated_candidate_count：完成身份融合和 RRF 后、规则过滤前的数量。
        merged_candidate_count：被身份融合合并的重复来源记录数量。
        filtered_candidate_count：被确定性规则过滤移除的论文数量。
        filter_reason_counts：按首个失败规则汇总的过滤数量。
        work_family_count：过滤后候选中唯一版本族数量。
    """

    model_config = ConfigDict(extra="forbid")  # 阻止内部边界字段拼写错误被静默忽略。

    route_plan: SourceRoutePlan  # 保存来源调用前确定的执行计划。
    query_intent: QueryIntent  # 保存本轮实际执行且已校验的查询意图。
    papers: list[PaperRecord] = Field(default_factory=list)  # 保存规则过滤后、BGE-M3 前的论文候选。
    discoveries: list[SupplementalDiscoveryItem] = Field(default_factory=list)  # 保存独立网页发现项且禁止合并为论文。
    academic_source_counts: dict[str, int] = Field(default_factory=dict)  # 保存学术来源成功映射记录数。
    web_discovery_source_counts: dict[str, int] = Field(default_factory=dict)  # 保存网页来源成功发现数量。
    academic_source_errors: dict[str, str] = Field(default_factory=dict)  # 保存学术来源安全错误摘要。
    web_discovery_source_errors: dict[str, str] = Field(default_factory=dict)  # 保存网页来源安全错误摘要。
    cache_hit_count: int = Field(default=0, ge=0)  # 保存共享来源缓存本轮有效命中数。
    normalized_candidate_count: int = Field(ge=0)  # 保存进入身份融合前的统一论文记录数量。
    deduplicated_candidate_count: int = Field(ge=0)  # 保存身份融合和 RRF 后、规则过滤前数量。
    merged_candidate_count: int = Field(ge=0)  # 保存被合并到其他身份组的重复记录数量。
    filtered_candidate_count: int = Field(ge=0)  # 保存确定性规则移除数量。
    filter_reason_counts: dict[str, int] = Field(default_factory=dict)  # 保存每篇移除论文的首个失败原因统计。
    work_family_count: int = Field(ge=0)  # 保存过滤后候选中的唯一版本族数量。

    @property
    def source_counts(self) -> dict[str, int]:
        """返回保持路由类别分离后再合并的公共来源数量副本。"""
        return {**self.academic_source_counts, **self.web_discovery_source_counts}  # 兼容现有公共响应的混合来源计数字段。

    @property
    def source_errors(self) -> dict[str, str]:
        """返回保持路由类别分离后再合并的安全错误摘要副本。"""
        return {**self.academic_source_errors, **self.web_discovery_source_errors}  # 兼容现有公共响应的来源错误字段。

    @model_validator(mode="after")
    def validate_stage_boundaries(self) -> "CandidateGenerationResult":
        """校验来源覆盖、融合、过滤和候选数量关系。"""
        academic_sources = set(self.route_plan.academic_sources)  # 固化路由计划中的学术来源集合。
        web_sources = set(self.route_plan.web_discovery_sources)  # 固化路由计划中的网页发现来源集合。
        if set(self.academic_source_counts) != academic_sources:  # 成功或失败来源都必须有明确数量。
            raise ValueError("academic_source_counts 必须完整覆盖路由学术来源")  # 防止来源失败时遗漏零计数。
        if set(self.web_discovery_source_counts) != web_sources:  # 未启用网页来源时应保持空映射。
            raise ValueError("web_discovery_source_counts 必须完整覆盖路由网页来源")  # 防止网页统计混入学术来源。
        if not set(self.academic_source_errors).issubset(academic_sources):  # 错误只能属于已执行学术来源。
            raise ValueError("academic_source_errors 只能包含路由学术来源")  # 拒绝无法追溯的错误来源。
        if not set(self.web_discovery_source_errors).issubset(web_sources):  # 网页错误只能属于已执行网页来源。
            raise ValueError("web_discovery_source_errors 只能包含路由网页来源")  # 拒绝来源类别漂移。
        all_counts = [*self.academic_source_counts.values(), *self.web_discovery_source_counts.values()]  # 汇总两类来源计数供统一类型校验。
        if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in all_counts):  # 仅允许非负整数来源数量。
            raise ValueError("来源数量必须只包含非负整数")  # 防止布尔值或无效计数污染 usage。
        if sum(self.academic_source_counts.values()) != self.normalized_candidate_count:  # 学术适配器输出应完整进入身份融合。
            raise ValueError("学术来源数量总和必须等于 normalized_candidate_count")  # 阻止把网页发现或原始响应数混入论文统计。
        if self.normalized_candidate_count != self.deduplicated_candidate_count + self.merged_candidate_count:  # 身份融合只保留或合并记录。
            raise ValueError("normalized_candidate_count 必须等于 deduplicated_candidate_count 加 merged_candidate_count")  # 防止融合统计丢失记录。
        if self.deduplicated_candidate_count != self.filtered_candidate_count + len(self.papers):  # 规则过滤前候选必须完整分为移除和保留。
            raise ValueError("deduplicated_candidate_count 必须等于 filtered_candidate_count 加 papers 数量")  # 保证 papers 就是真实排序输入集合。
        if any(not reason.strip() for reason in self.filter_reason_counts):  # 空白原因不能解释确定性过滤规则。
            raise ValueError("filter_reason_counts 不能包含空白原因")  # 拒绝不可审计原因。
        if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in self.filter_reason_counts.values()):  # 过滤计数必须为非负整数。
            raise ValueError("filter_reason_counts 必须只包含非负整数")  # 拒绝无效过滤数量。
        if sum(self.filter_reason_counts.values()) != self.filtered_candidate_count:  # 每篇过滤论文只记录首个失败规则。
            raise ValueError("filter_reason_counts 总和必须等于 filtered_candidate_count")  # 防止过滤原因遗漏或重复。
        if sum(self.web_discovery_source_counts.values()) != len(self.discoveries):  # 网页结果不经过论文去重或过滤。
            raise ValueError("网页来源数量总和必须等于 discoveries 数量")  # 保证补充发现统计独立完整。
        actual_work_family_count = len({paper.work_family_id for paper in self.papers if paper.work_family_id})  # 仅统计过滤后论文中的明确版本族。
        if self.work_family_count != actual_work_family_count:  # 版本族统计必须对应真实排序输入候选。
            raise ValueError("work_family_count 必须等于过滤后候选的唯一版本族数量")  # 防止复用过滤前统计。
        return self  # 返回通过全部候选生成边界校验的结果。
