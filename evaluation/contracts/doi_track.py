"""定义 LongEval DOI-strict 离线评分的稳定输出契约。"""

from pydantic import BaseModel, Field


class DoiTrackCutoffMetrics(BaseModel):
    """保存 DOI-strict Track 在单个 Top-K 下的查询或聚合指标。"""

    k: int = Field(ge=1)
    true_positive: int = Field(ge=0)
    predicted_doi_count: int = Field(ge=0)
    gold_doi_count: int = Field(ge=1)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)
    hit: bool
    ndcg: float = Field(ge=0.0, le=1.0)


class DoiTrackQueryMetrics(BaseModel):
    """保存一条 GoldQuery 的 DOI-strict 评分分解。"""

    query_id: str = Field(min_length=1)
    matching_policy: str = "doi-strict-v1"
    cutoffs: dict[int, DoiTrackCutoffMetrics]
    mrr: float = Field(ge=0.0, le=1.0)
    gold_doi_count: int = Field(ge=1)
    prediction_paper_count: int = Field(ge=0)
    valid_prediction_doi_count: int = Field(ge=0)
    invalid_or_missing_prediction_doi_count: int = Field(ge=0)
    duplicate_prediction_doi_count: int = Field(ge=0)
    missing_prediction: bool = False


class DoiTrackAggregateCutoffMetrics(BaseModel):
    """保存 Top-K 的 DOI-strict Macro/Micro 指标与零命中率。"""

    k: int = Field(ge=1)
    macro_precision: float = Field(ge=0.0, le=1.0)
    macro_recall: float = Field(ge=0.0, le=1.0)
    macro_f1: float = Field(ge=0.0, le=1.0)
    micro_precision: float = Field(ge=0.0, le=1.0)
    micro_recall: float = Field(ge=0.0, le=1.0)
    micro_f1: float = Field(ge=0.0, le=1.0)
    mean_ndcg: float = Field(ge=0.0, le=1.0)
    hit_query_count: int = Field(ge=0)
    zero_hit_query_rate: float = Field(ge=0.0, le=1.0)


class DoiTrackSummary(BaseModel):
    """保存一次不含任何在线调用的 DOI-strict 评分报告。"""

    schema_version: str = "doi-track-score-v1"
    matching_policy: str = "doi-strict-v1"
    query_count: int = Field(ge=0)
    predicted_query_count: int = Field(ge=0)
    cutoffs: dict[int, DoiTrackAggregateCutoffMetrics]
    mean_mrr: float = Field(ge=0.0, le=1.0)
    prediction_doi_coverage: float = Field(ge=0.0, le=1.0)
    query_metrics: list[DoiTrackQueryMetrics] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
