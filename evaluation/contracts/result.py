"""定义评测查询级、聚合级与报告级结果契约。"""

from typing import Literal  # 限制代理分标签，避免误写成官方分。

from pydantic import BaseModel, Field  # 提供可序列化的稳定评测结果。


class CutoffMetrics(BaseModel):
    """保存单条查询在一个 Top-K 截断下的检索指标。"""

    k: int = Field(ge=1)  # 保存当前评分截断。
    true_positive: int = Field(ge=0)  # 保存命中的唯一金标论文数。
    predicted_count: int = Field(ge=0)  # 保存截断内参与评分的唯一预测数。
    relevant_count: int = Field(ge=0)  # 保存当前查询的唯一金标论文总数。
    precision: float = Field(ge=0.0, le=1.0)  # 保存精确率。
    recall: float = Field(ge=0.0, le=1.0)  # 保存召回率。
    f1: float = Field(ge=0.0, le=1.0)  # 保存 F1。


class QueryMetrics(BaseModel):
    """保存单条查询的去重、检索和排序指标。"""

    query_id: str = Field(min_length=1)  # 关联输入查询。
    cutoffs: dict[int, CutoffMetrics]  # 保存各 Top-K 指标。
    mrr: float = Field(ge=0.0, le=1.0)  # 保存首个相关结果倒数排名。
    ndcg_at_k: dict[int, float]  # 保存各 Top-K 的二元 nDCG。
    unique_gold_count: int = Field(ge=0)  # 保存去重后的金标数量。
    unique_prediction_count: int = Field(ge=0)  # 保存去重后的预测数量。
    duplicate_prediction_count: int = Field(ge=0)  # 保存被识别出的重复预测数量。
    missing_identifier_count: int = Field(ge=0)  # 保存缺少强标识符的预测数量。
    missing_prediction: bool = False  # 标记该金标查询是否完全缺少预测记录。


class AggregateCutoffMetrics(BaseModel):
    """保存一个 Top-K 下的宏平均与微平均。"""

    k: int = Field(ge=1)  # 保存聚合截断。
    macro_precision: float = Field(ge=0.0, le=1.0)  # 保存查询级精确率宏平均。
    macro_recall: float = Field(ge=0.0, le=1.0)  # 保存查询级召回率宏平均。
    macro_f1: float = Field(ge=0.0, le=1.0)  # 保存查询级 F1 宏平均。
    micro_precision: float = Field(ge=0.0, le=1.0)  # 保存全局命中数计算的微精确率。
    micro_recall: float = Field(ge=0.0, le=1.0)  # 保存全局命中数计算的微召回率。
    micro_f1: float = Field(ge=0.0, le=1.0)  # 保存微平均 F1。


class RetrievalSummary(BaseModel):
    """保存整个 fixture 的检索与排序聚合结果。"""

    query_count: int = Field(ge=0)  # 保存金标查询数量。
    predicted_query_count: int = Field(ge=0)  # 保存具有预测记录的查询数量。
    cutoffs: dict[int, AggregateCutoffMetrics]  # 保存各截断聚合结果。
    mean_mrr: float = Field(ge=0.0, le=1.0)  # 保存查询 MRR 平均值。
    mean_ndcg_at_k: dict[int, float]  # 保存查询 nDCG 宏平均。


class EfficiencySummary(BaseModel):
    """保存原始效率统计和明确标记的本地代理分。"""

    academic_api_calls: int | None = Field(default=None, ge=0)  # 汇总已观测学术 API 逻辑调用数。
    actual_http_requests: int | None = Field(default=None, ge=0)  # 汇总包含重试的 HTTP 请求数。
    llm_calls: int | None = Field(default=None, ge=0)  # 汇总 LLM 调用数。
    total_tokens: int | None = Field(default=None, ge=0)  # 汇总 Token 数。
    latency_mean_ms: float | None = Field(default=None, ge=0)  # 保存已观测查询的平均耗时。
    latency_p95_ms: float | None = Field(default=None, ge=0)  # 保存已观测查询的 P95 耗时。
    retry_count: int | None = Field(default=None, ge=0)  # 汇总重试数。
    rate_limit_count: int | None = Field(default=None, ge=0)  # 汇总限流次数。
    cache_hit_count: int | None = Field(default=None, ge=0)  # 汇总缓存命中数。
    proxy_components: dict[str, float | None] = Field(default_factory=dict)  # 保存透明的线性代理分组件。
    proxy_score: float | None = Field(default=None, ge=0.0, le=1.0)  # 保存完整观测下的本地效率代理分。
    proxy_label: Literal["本地效率代理分（非官方）"] = "本地效率代理分（非官方）"  # 强制报告显示非官方属性。
    missing_fields: list[str] = Field(default_factory=list)  # 保存无法从输入恢复的观测字段。


class StructureQueryScore(BaseModel):
    """保存单条查询的确定性结构代理分。"""

    query_id: str = Field(min_length=1)  # 关联输入查询。
    ranked_list_legality: float = Field(ge=0.0, le=1.0)  # 衡量非空且无重复的有序列表。
    field_completeness: float = Field(ge=0.0, le=1.0)  # 衡量展示与身份字段完整度。
    relation_legality: float = Field(ge=0.0, le=1.0)  # 衡量关系和分类是否只引用集合内论文。
    proxy_score: float = Field(ge=0.0, le=1.0)  # 保存确定性加权结构代理分。


class StructureSummary(BaseModel):
    """保存结构化输出的本地代理分汇总。"""

    mean_proxy_score: float = Field(ge=0.0, le=1.0)  # 保存查询级结构分平均值。
    proxy_label: Literal["本地结构代理分（非官方）"] = "本地结构代理分（非官方）"  # 强制报告显示非官方属性。
    query_scores: list[StructureQueryScore] = Field(default_factory=list)  # 保存可审计的查询级分解。


class EvaluationSummary(BaseModel):
    """保存一次完全离线 fixture 评测的可发布报告数据。"""

    schema_version: str = "1.0"  # 固定第一阶段报告契约版本。
    generated_at: str  # 保存带时区的生成时间。
    retrieval: RetrievalSummary  # 保存检索和排序指标。
    efficiency: EfficiencySummary  # 保存原始效率与本地代理分。
    structure: StructureSummary  # 保存确定性结构代理分。
    local_composite_proxy_score: float | None = Field(default=None, ge=0.0, le=1.0)  # 保存非官方综合代理分。
    composite_proxy_label: Literal["本地综合代理分（非官方）"] = "本地综合代理分（非官方）"  # 明确禁止冒充官方总分。
    query_metrics: list[QueryMetrics] = Field(default_factory=list)  # 保存 JSONL 明细所需查询级指标。
    warnings: list[str] = Field(default_factory=list)  # 保存缺失观测和代理分解释。
