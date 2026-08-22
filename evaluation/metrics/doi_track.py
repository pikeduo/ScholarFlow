"""计算 LongEval DOI-strict Track 的纯本地检索与排序指标。"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from backend.app.models.paper_identity import normalize_doi
from evaluation.contracts.doi_track import DoiTrackAggregateCutoffMetrics, DoiTrackCutoffMetrics, DoiTrackQueryMetrics, DoiTrackSummary
from evaluation.contracts.gold import GoldQuery
from evaluation.contracts.prediction import PredictionRecord


MATCHING_POLICY = "doi-strict-v1"
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", flags=re.IGNORECASE)


def normalize_strict_doi(value: str | None) -> str | None:
    """仅接受可规范化且满足 DOI 基本语法的文本，不对缺失标识做回退。"""
    normalized = normalize_doi(value)
    if normalized is None or _DOI_PATTERN.fullmatch(normalized) is None:
        return None
    return normalized


def score_doi_track(gold_queries: Sequence[GoldQuery], predictions: Sequence[PredictionRecord], *, cutoffs: Sequence[int] = (5, 10, 20)) -> DoiTrackSummary:
    """对已导入 DOI Gold 与本地预测执行严格 DOI 匹配，不访问任何外部资源。"""
    normalized_cutoffs = _normalize_cutoffs(cutoffs)
    gold_by_query = _index_by_query_id(gold_queries, "GoldQuery")
    prediction_by_query = _index_by_query_id(predictions, "PredictionRecord")
    extra_ids = sorted(set(prediction_by_query) - set(gold_by_query))
    if extra_ids:
        raise ValueError(f"预测包含未出现在 DOI Gold 中的 query_id: {', '.join(extra_ids)}")
    query_metrics = [score_doi_query(gold, prediction_by_query.get(gold.query_id), normalized_cutoffs) for gold in gold_queries]
    query_count = len(query_metrics)
    if query_count == 0:
        raise ValueError("DOI Gold 不能为空")
    valid_prediction_dois = sum(item.valid_prediction_doi_count for item in query_metrics)
    prediction_papers = sum(item.prediction_paper_count for item in query_metrics)
    return DoiTrackSummary(
        query_count=query_count,
        predicted_query_count=sum(not item.missing_prediction for item in query_metrics),
        cutoffs=_aggregate(query_metrics, normalized_cutoffs),
        mean_mrr=sum(item.mrr for item in query_metrics) / query_count,
        prediction_doi_coverage=valid_prediction_dois / prediction_papers if prediction_papers else 0.0,
        query_metrics=query_metrics,
        warnings=["仅规范化且语法有效的 DOI 可命中；标题、作者、arXiv、PMID、来源 ID 和语义相似性均不参与匹配。", "Prediction 中缺 DOI、非法 DOI 或重复 DOI 会保留在审计计数中，但不能扩大命中或 Precision 分母。"],
    )


def score_doi_query(gold_query: GoldQuery, prediction: PredictionRecord | None, cutoffs: Sequence[int]) -> DoiTrackQueryMetrics:
    """计算单查询 DOI-strict 指标，保持原预测排名以计算 MRR/nDCG。"""
    gold_dois = _gold_dois(gold_query)
    ranked_dois, invalid_or_missing_count, duplicate_count = _ranked_prediction_dois(prediction)
    cutoff_metrics = {k: _score_cutoff(gold_dois, ranked_dois, k) for k in cutoffs}
    first_hit_rank = next((rank for rank, doi in enumerate(ranked_dois, start=1) if doi is not None and doi in gold_dois), None)
    return DoiTrackQueryMetrics(
        query_id=gold_query.query_id,
        cutoffs=cutoff_metrics,
        mrr=1 / first_hit_rank if first_hit_rank is not None else 0.0,
        gold_doi_count=len(gold_dois),
        prediction_paper_count=len(prediction.papers) if prediction is not None else 0,
        valid_prediction_doi_count=sum(doi is not None for doi in ranked_dois),
        invalid_or_missing_prediction_doi_count=invalid_or_missing_count,
        duplicate_prediction_doi_count=duplicate_count,
        missing_prediction=prediction is None,
    )


def _gold_dois(gold_query: GoldQuery) -> set[str]:
    """验证 DOI Gold 完整性，拒绝非 DOI、重复 DOI 或空分母。"""
    normalized_dois: list[str] = []
    for paper in gold_query.relevant_papers:
        doi = normalize_strict_doi(paper.doi)
        if doi is None:
            raise ValueError(f"DOI Gold {gold_query.query_id} 包含缺失或非法 DOI")
        normalized_dois.append(doi)
    if not normalized_dois:
        raise ValueError(f"DOI Gold {gold_query.query_id} 不包含相关 DOI")
    if len(set(normalized_dois)) != len(normalized_dois):
        raise ValueError(f"DOI Gold {gold_query.query_id} 包含重复 DOI")
    return set(normalized_dois)


def _ranked_prediction_dois(prediction: PredictionRecord | None) -> tuple[list[str | None], int, int]:
    """保留原排名位置，将缺失、非法和重复 DOI 显式排除出命中集合。"""
    if prediction is None:
        return [], 0, 0
    seen_dois: set[str] = set()
    ranked_dois: list[str | None] = []
    invalid_or_missing_count = 0
    duplicate_count = 0
    for paper in prediction.papers:
        doi = normalize_strict_doi(paper.doi)
        if doi is None:
            invalid_or_missing_count += 1
            ranked_dois.append(None)
        elif doi in seen_dois:
            duplicate_count += 1
            ranked_dois.append(None)
        else:
            seen_dois.add(doi)
            ranked_dois.append(doi)
    return ranked_dois, invalid_or_missing_count, duplicate_count


def _score_cutoff(gold_dois: set[str], ranked_dois: Sequence[str | None], k: int) -> DoiTrackCutoffMetrics:
    """以原始排名的前 K 篇论文计算唯一有效预测 DOI 的 P/R/F1、Hit 与 nDCG。"""
    prefix = ranked_dois[:k]
    predicted_dois = {doi for doi in prefix if doi is not None}
    true_positive = len(predicted_dois & gold_dois)
    predicted_count = len(predicted_dois)
    precision = true_positive / predicted_count if predicted_count else 0.0
    recall = true_positive / len(gold_dois)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    dcg = sum(1 / _log2(rank + 1) for rank, doi in enumerate(prefix, start=1) if doi is not None and doi in gold_dois)
    ideal_dcg = sum(1 / _log2(rank + 1) for rank in range(1, min(k, len(gold_dois)) + 1))
    return DoiTrackCutoffMetrics(k=k, true_positive=true_positive, predicted_doi_count=predicted_count, gold_doi_count=len(gold_dois), precision=precision, recall=recall, f1=f1, hit=true_positive > 0, ndcg=dcg / ideal_dcg if ideal_dcg else 0.0)


def _aggregate(query_metrics: Sequence[DoiTrackQueryMetrics], cutoffs: Sequence[int]) -> dict[int, DoiTrackAggregateCutoffMetrics]:
    """按查询等权计算 Macro，并按命中、预测 DOI、Gold DOI 总量计算 Micro。"""
    aggregate: dict[int, DoiTrackAggregateCutoffMetrics] = {}
    query_count = len(query_metrics)
    for k in cutoffs:
        records = [item.cutoffs[k] for item in query_metrics]
        true_positive = sum(item.true_positive for item in records)
        predicted_count = sum(item.predicted_doi_count for item in records)
        gold_count = sum(item.gold_doi_count for item in records)
        micro_precision = true_positive / predicted_count if predicted_count else 0.0
        micro_recall = true_positive / gold_count if gold_count else 0.0
        aggregate[k] = DoiTrackAggregateCutoffMetrics(
            k=k,
            macro_precision=sum(item.precision for item in records) / query_count,
            macro_recall=sum(item.recall for item in records) / query_count,
            macro_f1=sum(item.f1 for item in records) / query_count,
            micro_precision=micro_precision,
            micro_recall=micro_recall,
            micro_f1=2 * micro_precision * micro_recall / (micro_precision + micro_recall) if micro_precision + micro_recall else 0.0,
            mean_ndcg=sum(item.ndcg for item in records) / query_count,
            hit_query_count=sum(item.hit for item in records),
            zero_hit_query_rate=1 - sum(item.hit for item in records) / query_count,
        )
    return aggregate


def _normalize_cutoffs(cutoffs: Sequence[int]) -> tuple[int, ...]:
    """确认 Top-K 非空、为正整数，并用排序去重结果冻结输出顺序。"""
    if not cutoffs or any(isinstance(k, bool) or not isinstance(k, int) or k < 1 for k in cutoffs):
        raise ValueError("cutoffs 必须包含至少一个正整数")
    return tuple(sorted(set(cutoffs)))


def _index_by_query_id(records: Sequence[GoldQuery] | Sequence[PredictionRecord], record_label: str) -> dict[str, GoldQuery | PredictionRecord]:
    """按 query_id 建立唯一索引，拒绝重复分母或重复预测。"""
    index: dict[str, GoldQuery | PredictionRecord] = {}
    for record in records:
        if record.query_id in index:
            raise ValueError(f"{record_label} 包含重复 query_id: {record.query_id}")
        index[record.query_id] = record
    return index


def _log2(value: int) -> float:
    """返回正整数的以 2 为底对数，避免为简单指标引入额外数值依赖。"""
    return math.log2(value)
