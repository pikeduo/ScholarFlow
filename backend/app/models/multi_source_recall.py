"""定义多源召回协调阶段输出的论文、网页发现与来源统计契约。"""

from pydantic import BaseModel, Field  # 提供多源协调结果的结构化校验。

from backend.app.models.discovery import SupplementalDiscoveryItem  # 保存不可合并的补充网页发现结果。
from backend.app.models.paper import PaperRecord  # 保存可进入后续规范化与去重阶段的论文记录。
from backend.app.models.source_routing import SourceRoutePlan  # 关联当前召回使用的可审计来源计划。


class MultiSourceRecallResult(BaseModel):
    """保存一次多源召回的分流结果、来源统计和安全降级信息。

    属性：
        route_plan：本次执行前生成的来源选择计划。
        papers：完成跨来源身份融合后的统一论文记录。
        discoveries：不能合并为论文的补充网页发现项。
        source_counts：每个已选来源成功返回的条目数量。
        source_errors：来源调用失败或未注册时的安全错误摘要。
        raw_paper_count：各学术来源返回并进入融合前的原始论文数量。
        merged_paper_count：被合并到其他身份组的重复来源记录数量。
        filtered_paper_count：融合后因确定性规则被移除的论文数量。
        filter_reason_counts：按首个未通过规则汇总的移除数量。
        semantic_truncated_count：BGE-M3 粗排候选截断数量。
        semantic_ranking_error：语义模型不可用时的安全降级摘要。
        cross_encoder_truncated_count：Cross Encoder 重排候选截断数量。
        cross_encoder_ranking_error：Cross Encoder 模型不可用时的安全降级摘要。
        llm_truncated_count：LLM 核验通过但超出最终结果上限的候选数量。
        llm_rejected_count：LLM 明确判定不满足硬约束的候选数量。
        llm_ranking_error：LLM 不可用时的安全降级摘要。
        llm_model_name：实际或配置使用的 LLM 名称。
        llm_prompt_tokens：本次 LLM 精排输入 Token 数量。
        llm_completion_tokens：本次 LLM 精排输出 Token 数量。
        work_family_count：融合结果中可识别版本族的唯一数量。
    """

    route_plan: SourceRoutePlan  # 保留实际执行的来源选择与预先降级说明。
    papers: list[PaperRecord] = Field(default_factory=list)  # 保存按首次身份组顺序排列的融合论文记录。
    discoveries: list[SupplementalDiscoveryItem] = Field(default_factory=list)  # 保存独立于论文集合的网页发现结果。
    source_counts: dict[str, int] = Field(default_factory=dict)  # 保存每个来源的成功结果数。
    source_errors: dict[str, str] = Field(default_factory=dict)  # 保存不含密钥、路径和响应正文的来源错误摘要。
    raw_paper_count: int = Field(default=0, ge=0)  # 保存跨来源融合前的原始学术论文数量。
    merged_paper_count: int = Field(default=0, ge=0)  # 保存因身份融合而被合并的重复来源记录数量。
    filtered_paper_count: int = Field(default=0, ge=0)  # 保存融合后因确定性规则被移除的论文数量。
    filter_reason_counts: dict[str, int] = Field(default_factory=dict)  # 保存按首个失败规则汇总的安全过滤统计。
    semantic_truncated_count: int = Field(default=0, ge=0)  # 保存 BGE-M3 粗排候选截断数量。
    semantic_ranking_error: str | None = None  # 保存语义模型不可用时不含内部细节的降级摘要。
    cross_encoder_truncated_count: int = Field(default=0, ge=0)  # 保存 Cross Encoder 重排候选截断数量。
    cross_encoder_ranking_error: str | None = None  # 保存 Cross Encoder 模型不可用时不含内部细节的降级摘要。
    llm_truncated_count: int = Field(default=0, ge=0)  # 保存核验通过但超出最终结果数量的候选数。
    llm_rejected_count: int = Field(default=0, ge=0)  # 保存明确不满足语义硬约束的候选数。
    llm_ranking_error: str | None = None  # 保存 LLM 不可用时不含内部细节的降级摘要。
    llm_model_name: str | None = None  # 保存实际或配置使用的 LLM 名称供运行审计。
    llm_prompt_tokens: int = Field(default=0, ge=0)  # 保存本阶段输入 Token 数量。
    llm_completion_tokens: int = Field(default=0, ge=0)  # 保存本阶段输出 Token 数量。
    work_family_count: int = Field(default=0, ge=0)  # 保存融合论文中唯一版本族标识的数量。
