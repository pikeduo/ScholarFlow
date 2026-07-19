"""定义复用候选快照的离线排序消融配置、阶段结果与执行计划。"""

from typing import Literal  # 限制阶段名称和实验类型。

from pydantic import BaseModel, ConfigDict, Field, model_validator  # 校验离线矩阵和结果数量。

from evaluation.contracts.prediction import PredictionRecord, RankingConfig  # 复用排序数量与预测契约。


class AblationExperiment(BaseModel):
    """保存一组可选本地排序和 DeepSeek 核验的消融配置。"""

    model_config = ConfigDict(extra="forbid")  # 拒绝配置字段拼写错误。

    experiment_id: str = Field(min_length=1)  # 保存矩阵内唯一配置标识。
    label: str = Field(min_length=1)  # 保存人工可读配置名称。
    ranking_config: RankingConfig  # 保存各阶段开关和候选保留数量。

    @model_validator(mode="after")
    def validate_deepseek_target_boundary(self) -> "AblationExperiment":
        """要求启用 DeepSeek 的实验保留明确且可审计的最终候选数量。"""
        if self.ranking_config.deepseek_enabled and self.ranking_config.target_paper_count < 1:  # LLM 核验必须有非零目标集合。
            raise ValueError("启用 DeepSeek 的实验必须设置正数 target_paper_count")  # 防止无意义的外部调用配置。
        return self  # 真实调用仍由执行器显式授权和预估确认控制。


class AblationMatrix(BaseModel):
    """保存共享来源召回与评分口径的一组离线排序实验。"""

    model_config = ConfigDict(extra="forbid")  # 保证矩阵 JSON 全部字段进入审计。

    matrix_id: str = Field(min_length=1)  # 保存矩阵唯一标识。
    experiments: list[AblationExperiment] = Field(min_length=1)  # 保存待执行配置。

    @model_validator(mode="after")
    def validate_shared_online_and_scoring_config(self) -> "AblationMatrix":
        """要求矩阵内在线召回规模和评分 Top-K 完全一致。"""
        experiment_ids = [experiment.experiment_id for experiment in self.experiments]  # 收集实验标识。
        if len(set(experiment_ids)) != len(experiment_ids):  # 重复标识会覆盖结果归档。
            raise ValueError("experiment_id 不得重复")  # 拒绝歧义矩阵。
        source_counts = {experiment.ranking_config.source_recall_count for experiment in self.experiments}  # 收集在线召回规模。
        if len(source_counts) != 1:  # 同一快照不可能来自多个召回规模。
            raise ValueError("同一消融矩阵必须共享 source_recall_count")  # 阻止配置偷换在线候选。
        evaluation_cutoffs = {tuple(experiment.ranking_config.evaluation_top_k) for experiment in self.experiments}  # 收集评分口径。
        if len(evaluation_cutoffs) != 1:  # 同一矩阵必须横向可比。
            raise ValueError("同一消融矩阵必须共享 evaluation_top_k")  # 阻止不同截断混入同一比较。
        return self  # 返回通过共享边界校验的矩阵。


class RankingScoreBatch(BaseModel):
    """保存可替换本地打分器对一批候选的原始输出。"""

    model_config = ConfigDict(extra="forbid")  # 打分适配器不得返回未声明统计字段。

    scores: list[float]  # 保存与输入候选严格等长的原始相关性分数。
    model_name: str = Field(min_length=1)  # 保存实际模型或测试替身名称。
    latency_ms: float = Field(ge=0)  # 保存本地打分耗时。
    device: str | None = None  # 保存实际 CPU/GPU 设备。
    batch_size: int | None = Field(default=None, ge=1)  # 保存推理批大小。
    oom_retry_count: int = Field(default=0, ge=0)  # 保存 OOM 降批重试次数。


class RankingStageTrace(BaseModel):
    """保存一个本地排序阶段的输入、输出和运行统计。"""

    model_config = ConfigDict(extra="forbid")  # 稳定阶段统计契约。

    stage: Literal["rrf", "bge_m3", "cross_encoder", "deepseek", "target"]  # 保存明确阶段名称。
    enabled: bool  # 标记该阶段是否执行本地打分。
    input_count: int = Field(ge=0)  # 保存阶段输入候选数。
    output_count: int = Field(ge=0)  # 保存阶段保留候选数。
    candidate_limit: int | None = Field(default=None, ge=1)  # 保存该阶段可配置保留上限。
    latency_ms: float | None = Field(default=None, ge=0)  # 保存已执行模型阶段耗时。
    model_name: str | None = None  # 保存实际模型或测试替身名称。
    device: str | None = None  # 保存实际执行设备。
    batch_size: int | None = Field(default=None, ge=1)  # 保存实际批大小。
    oom_retry_count: int | None = Field(default=None, ge=0)  # 保存实际 OOM 重试次数。


class OfflineAblationResult(BaseModel):
    """保存一条快照在一个离线配置下的预测和阶段审计信息。"""

    model_config = ConfigDict(extra="forbid")  # 防止结果归档静默丢字段。

    matrix_id: str = Field(min_length=1)  # 关联消融矩阵。
    experiment_id: str = Field(min_length=1)  # 关联矩阵配置。
    snapshot_id: str = Field(min_length=1)  # 关联唯一候选快照。
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")  # 固定本次排序实际读取的内容哈希。
    query_id: str = Field(min_length=1)  # 关联查询。
    prediction: PredictionRecord  # 保存可直接交给第一阶段指标模块的预测。
    stage_traces: list[RankingStageTrace]  # 保存 RRF、本地排序和最终截断统计。


class AblationPlan(BaseModel):
    """保存只读快照与矩阵组合形成的离线任务计划。"""

    model_config = ConfigDict(extra="forbid")  # 保持零 API、零 DeepSeek 计划可审计。

    matrix_id: str = Field(min_length=1)  # 关联配置矩阵。
    snapshot_ids: list[str]  # 保存唯一快照标识。
    snapshot_hashes: dict[str, str]  # 保存每份快照的完整性摘要。
    experiment_ids: list[str]  # 保存矩阵配置顺序。
    task_count: int = Field(ge=0)  # 保存快照数乘配置数的任务总量。
    academic_api_calls: Literal[0] = 0  # 明确计划本身不会调用学术 API。
    deepseek_calls: Literal[0] = 0  # 明确第一轮矩阵不会调用 DeepSeek。


def build_standard_ablation_matrix(*, matrix_id: str = "local-ranking-abcd", source_recall_count: int = 50, semantic_top_k: int = 40, cross_encoder_top_k: int = 20, target_paper_count: int = 20, evaluation_top_k: list[int] | None = None) -> AblationMatrix:
    """生成共享在线候选与评分口径的标准 A/B/C/D 第一轮消融矩阵。"""
    cutoffs = list(evaluation_top_k or [5, 10, 20])  # 为每个配置复制评分截断，避免共享可变列表。
    common = {"source_recall_count": source_recall_count, "semantic_top_k": semantic_top_k, "cross_encoder_top_k": cross_encoder_top_k, "target_paper_count": target_paper_count, "evaluation_top_k": cutoffs, "deepseek_enabled": False}  # 固定共享数量和零 DeepSeek 边界。
    experiments = [
        AblationExperiment(experiment_id="A", label="RRF", ranking_config=RankingConfig(**common, semantic_ranking_enabled=False, cross_encoder_ranking_enabled=False)),  # 基线只使用快照 RRF 顺序。
        AblationExperiment(experiment_id="B", label="RRF + BGE-M3", ranking_config=RankingConfig(**common, semantic_ranking_enabled=True, cross_encoder_ranking_enabled=False)),  # 只执行 BGE-M3。
        AblationExperiment(experiment_id="C", label="RRF + Cross Encoder", ranking_config=RankingConfig(**common, semantic_ranking_enabled=False, cross_encoder_ranking_enabled=True)),  # Cross Encoder 直接读取完整快照。
        AblationExperiment(experiment_id="D", label="RRF + BGE-M3 + Cross Encoder", ranking_config=RankingConfig(**common, semantic_ranking_enabled=True, cross_encoder_ranking_enabled=True)),  # Cross Encoder 读取 BGE-M3 保留候选。
    ]
    return AblationMatrix(matrix_id=matrix_id, experiments=experiments)  # 返回通过共享配置校验的矩阵。
