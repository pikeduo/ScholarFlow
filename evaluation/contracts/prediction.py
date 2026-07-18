"""定义离线预测、排序配置和结构化输出契约。"""

from pydantic import BaseModel, Field, model_validator  # 提供配置范围与候选数量关系校验。

from evaluation.contracts.common import ClassificationRecord, EvaluationPaper, EvaluationUsage, RelationRecord  # 复用公共预测字段。


class RankingConfig(BaseModel):
    """明确区分在线召回、本地排序、最终输出和评分截断数量。"""

    source_recall_count: int = Field(default=50, ge=1, le=100)  # 保存每来源每轮召回上限。
    semantic_ranking_enabled: bool = False  # 保存是否执行 BGE-M3 推理。
    semantic_top_k: int = Field(default=60, ge=1)  # 保存 BGE-M3 阶段后保留数量。
    cross_encoder_ranking_enabled: bool = False  # 保存是否执行 Cross Encoder 推理。
    cross_encoder_top_k: int = Field(default=24, ge=1)  # 保存 Cross Encoder 阶段后保留数量。
    deepseek_enabled: bool = False  # 保存是否执行少量最优配置的 DeepSeek 对比。
    target_paper_count: int = Field(default=20, ge=1, le=100)  # 保存最终期望输出数量。
    evaluation_top_k: list[int] = Field(default_factory=lambda: [5, 10, 20], min_length=1)  # 保存一次离线计算的评分截断集合。

    @model_validator(mode="after")
    def validate_candidate_boundaries(self) -> "RankingConfig":
        """校验本地阶段保留数量与评分 Top-K 的稳定边界。

        返回：
            RankingConfig：数量关系可执行且 Top-K 已规范化的配置。
        异常：
            ValueError：后级候选数大于前级或 Top-K 非正时抛出。
        """
        if self.semantic_ranking_enabled and self.cross_encoder_ranking_enabled and self.cross_encoder_top_k > self.semantic_top_k:  # 两级模型都启用时后级候选不能超过上游数量。
            raise ValueError("cross_encoder_top_k 不能大于 semantic_top_k")  # 阻止不可执行的候选规模配置。
        if self.cross_encoder_ranking_enabled and self.target_paper_count > self.cross_encoder_top_k:  # 启用 Cross Encoder 时最终目标不能超过其输出。
            raise ValueError("target_paper_count 不能大于 cross_encoder_top_k")  # 保证关闭模型时的确定性截断也可满足目标。
        if self.semantic_ranking_enabled and not self.cross_encoder_ranking_enabled and self.target_paper_count > self.semantic_top_k:  # 仅启用 BGE-M3 时目标受其输出约束。
            raise ValueError("target_paper_count 不能大于 semantic_top_k")  # 防止最终阶段请求不存在的 BGE-M3 候选。
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in self.evaluation_top_k):  # 布尔值不能被视为整数 Top-K。
            raise ValueError("evaluation_top_k 必须只包含正整数")  # 返回稳定配置错误。
        self.evaluation_top_k = sorted(set(self.evaluation_top_k))  # 去重并排序以稳定报告列顺序。
        return self  # 返回规范化后的评测配置。


class PredictionRecord(BaseModel):
    """保存某条查询的一次离线预测及其可选观测信息。"""

    query_id: str = Field(min_length=1)  # 关联金标查询标识。
    snapshot_id: str | None = None  # 关联未来排序前候选快照，第一阶段允许为空。
    run_id: str | None = None  # 关联已有 ScholarFlow 运行快照，纯 fixture 允许为空。
    ranking_config: RankingConfig | None = None  # 保存本次排序配置；缺失预测允许为空。
    papers: list[EvaluationPaper] = Field(default_factory=list)  # 保存按预测相关性排序的论文列表。
    usage: EvaluationUsage = Field(default_factory=EvaluationUsage)  # 保存缺失值不会自动补零的效率观测。
    relations: list[RelationRecord] = Field(default_factory=list)  # 保存结果集合内的显式关系。
    classifications: list[ClassificationRecord] = Field(default_factory=list)  # 保存结果集合内的显式分类。
    warnings: list[str] = Field(default_factory=list)  # 保存安全评测警告。
