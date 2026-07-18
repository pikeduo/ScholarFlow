"""计算查询级 Precision、Recall、F1、MRR 与二元 nDCG。"""

import math  # 提供 DCG 对数折损。

from evaluation.contracts.gold import GoldQuery  # 接收统一金标查询。
from evaluation.contracts.prediction import PredictionRecord  # 接收统一预测记录。
from evaluation.contracts.result import CutoffMetrics, QueryMetrics  # 返回稳定查询级指标。
from evaluation.metrics.identifiers import deduplicate_papers, has_strong_identifier, papers_match  # 复用身份规则。


def _safe_ratio(numerator: float, denominator: float) -> float:
    """在空分母时按评测惯例返回零。"""
    return numerator / denominator if denominator else 0.0  # 空预测或空金标不产生虚假满分。


def _f1(precision: float, recall: float) -> float:
    """根据精确率与召回率计算调和平均。"""
    return _safe_ratio(2.0 * precision * recall, precision + recall)  # 两者都为零时稳定返回零。


def _relevance_vector(predicted: list, gold: list) -> list[int]:
    """按排名生成二元相关性并保证同一金标最多命中一次。"""
    matched_gold: set[int] = set()  # 防止重复预测重复得分。
    relevance: list[int] = []  # 保存与原始预测位置对齐的二元序列。
    for paper in predicted:  # 保持预测原始顺序和重复项占位。
        matched_index = next((index for index, gold_paper in enumerate(gold) if index not in matched_gold and papers_match(paper, gold_paper)), None)  # 查找首个未命中金标。
        if matched_index is None:  # 当前预测不命中或只是重复命中。
            relevance.append(0)  # 重复项不能再次计分。
            continue  # 处理下一排名。
        matched_gold.add(matched_index)  # 锁定唯一金标命中。
        relevance.append(1)  # 记录当前排名相关。
    return relevance  # 返回用于 MRR 和 nDCG 的序列。


def _ndcg(relevance: list[int], relevant_count: int, k: int) -> float:
    """计算给定截断下的二元 nDCG。"""
    dcg = sum(value / math.log2(index + 2) for index, value in enumerate(relevance[:k]))  # 对靠前命中给予更高权重。
    ideal_count = min(relevant_count, k)  # 理想列表最多容纳 K 个相关结果。
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_count))  # 构造二元理想 DCG。
    return _safe_ratio(dcg, idcg)  # 无金标时返回零而非虚假满分。


def evaluate_query(gold_query: GoldQuery, prediction: PredictionRecord | None, cutoffs: list[int]) -> QueryMetrics:
    """评测一条金标查询，缺失预测按空列表计分。"""
    unique_gold, _ = deduplicate_papers(gold_query.relevant_papers)  # 防止重复金标扩大召回分母。
    predicted = prediction.papers if prediction is not None else []  # 缺失预测保持显式空列表。
    unique_prediction, duplicate_count = deduplicate_papers(predicted)  # 汇总整表唯一数与重复数。
    relevance = _relevance_vector(predicted, unique_gold)  # 保留重复项对排名的真实影响。
    cutoff_metrics: dict[int, CutoffMetrics] = {}  # 收集各截断指标。
    ndcg_at_k: dict[int, float] = {}  # 收集各截断排序指标。
    for k in sorted(set(cutoffs)):  # 稳定报告顺序并避免重复计算。
        unique_at_k, _ = deduplicate_papers(predicted[:k])  # 截断后去重且不以后续候选补位。
        true_positive = sum(relevance[:k])  # 每个金标最多贡献一次命中。
        precision = _safe_ratio(true_positive, len(unique_at_k))  # 只以截断内唯一预测为分母。
        recall = _safe_ratio(true_positive, len(unique_gold))  # 以去重后金标总数为分母。
        cutoff_metrics[k] = CutoffMetrics(k=k, true_positive=true_positive, predicted_count=len(unique_at_k), relevant_count=len(unique_gold), precision=precision, recall=recall, f1=_f1(precision, recall))  # 固化查询级统计。
        ndcg_at_k[k] = _ndcg(relevance, len(unique_gold), k)  # 计算同一截断下的二元 nDCG。
    first_relevant_rank = next((index + 1 for index, value in enumerate(relevance) if value), None)  # 查找首个相关结果排名。
    mrr = 1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0  # 未命中时 MRR 为零。
    return QueryMetrics(query_id=gold_query.query_id, cutoffs=cutoff_metrics, mrr=mrr, ndcg_at_k=ndcg_at_k, unique_gold_count=len(unique_gold), unique_prediction_count=len(unique_prediction), duplicate_prediction_count=duplicate_count, missing_identifier_count=sum(not has_strong_identifier(paper) for paper in predicted), missing_prediction=prediction is None)  # 返回完整可审计结果。
