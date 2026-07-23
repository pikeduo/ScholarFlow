"""聚合查询级检索与排序指标。"""

from statistics import fmean  # 计算查询级宏平均。

from evaluation.contracts.result import AggregateCutoffMetrics, QueryMetrics, RetrievalSummary  # 使用稳定结果契约。


def _ratio(numerator: float, denominator: float) -> float:
    """为空分母提供确定性的零值。"""
    return numerator / denominator if denominator else 0.0  # 防止空 fixture 抛出异常。


def _f1(precision: float, recall: float) -> float:
    """计算微平均精确率与召回率的 F1。"""
    return _ratio(2.0 * precision * recall, precision + recall)  # 两者为零时返回零。


def aggregate_query_metrics(query_metrics: list[QueryMetrics], cutoffs: list[int]) -> RetrievalSummary:
    """同时计算宏平均、微平均、平均 MRR 与平均 nDCG。"""
    aggregate_cutoffs: dict[int, AggregateCutoffMetrics] = {}  # 收集各 Top-K 聚合统计。
    mean_ndcg: dict[int, float] = {}  # 收集各 Top-K 的 nDCG 宏平均。
    for k in sorted(set(cutoffs)):  # 确保输出顺序稳定。
        rows = [metrics.cutoffs[k] for metrics in query_metrics]  # 读取同一截断的查询行。
        true_positive = sum(row.true_positive for row in rows)  # 汇总全局命中数。
        predicted_count = sum(row.predicted_count for row in rows)  # 汇总唯一预测分母。
        relevant_count = sum(row.relevant_count for row in rows)  # 汇总唯一金标分母。
        micro_precision = _ratio(true_positive, predicted_count)  # 计算微精确率。
        micro_recall = _ratio(true_positive, relevant_count)  # 计算微召回率。
        aggregate_cutoffs[k] = AggregateCutoffMetrics(k=k, macro_precision=fmean(row.precision for row in rows) if rows else 0.0, macro_recall=fmean(row.recall for row in rows) if rows else 0.0, macro_f1=fmean(row.f1 for row in rows) if rows else 0.0, micro_precision=micro_precision, micro_recall=micro_recall, micro_f1=_f1(micro_precision, micro_recall))  # 固化宏微指标。
        mean_ndcg[k] = fmean(metrics.ndcg_at_k[k] for metrics in query_metrics) if query_metrics else 0.0  # 计算查询级 nDCG 平均。
    return RetrievalSummary(query_count=len(query_metrics), predicted_query_count=sum(not metrics.missing_prediction for metrics in query_metrics), cutoffs=aggregate_cutoffs, mean_mrr=fmean(metrics.mrr for metrics in query_metrics) if query_metrics else 0.0, mean_ndcg_at_k=mean_ndcg)  # 返回聚合结果。
