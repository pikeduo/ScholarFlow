"""测试查询级和聚合级检索、排序指标。"""

import math  # 计算预期二元 nDCG。

import pytest  # 提供浮点近似断言。

from evaluation.contracts.common import EvaluationPaper  # 构造合成论文。
from evaluation.contracts.gold import GoldQuery  # 构造金标查询。
from evaluation.contracts.prediction import PredictionRecord  # 构造预测排序。
from evaluation.metrics.aggregate import aggregate_query_metrics  # 验证宏微聚合。
from evaluation.metrics.retrieval import evaluate_query  # 验证查询级指标。


def _paper(doi: str) -> EvaluationPaper:
    """创建具有稳定 DOI 的最小测试论文。"""
    return EvaluationPaper(doi=doi, title=doi)  # DOI 同时作为可读测试标题。


def test_duplicate_predictions_keep_rank_positions_without_backfill() -> None:
    """重复项应占据原排名位置且不能重复命中同一金标。"""
    gold = GoldQuery(query_id="q1", query="fixture", relevant_papers=[_paper("10.1/a"), _paper("10.1/b")])  # 构造两个相关金标。
    prediction = PredictionRecord(query_id="q1", papers=[_paper("10.1/a"), _paper("doi:10.1/a"), _paper("10.1/x"), _paper("10.1/b")])  # 在第二位插入重复预测。
    metrics = evaluate_query(gold, prediction, [3, 4])  # 计算两个截断。
    assert metrics.cutoffs[3].true_positive == 1  # 重复 A 不得再次命中。
    assert metrics.cutoffs[3].predicted_count == 2  # 截断内唯一预测为 A 和 X。
    assert metrics.cutoffs[3].precision == pytest.approx(0.5)  # Precision 以唯一预测为分母。
    assert metrics.cutoffs[3].recall == pytest.approx(0.5)  # 三位内只找到一半金标。
    assert metrics.cutoffs[3].f1 == pytest.approx(0.5)  # P 与 R 相同时 F1 相同。
    assert metrics.duplicate_prediction_count == 1  # 整表重复数量被记录。
    assert metrics.mrr == pytest.approx(1.0)  # 首位即为相关论文。
    expected_ndcg = (1.0 + 1.0 / math.log2(5)) / (1.0 + 1.0 / math.log2(3))  # 第二个命中位于第四名。
    assert metrics.ndcg_at_k[4] == pytest.approx(expected_ndcg)  # 重复项真实降低排序质量。


def test_missing_prediction_participates_in_macro_and_micro_metrics() -> None:
    """缺失预测不得从宏平均或微平均分母中消失。"""
    first_gold = GoldQuery(query_id="q1", query="one", relevant_papers=[_paper("10.1/a")])  # 第一条查询有完整命中。
    second_gold = GoldQuery(query_id="q2", query="two", relevant_papers=[_paper("10.1/b")])  # 第二条查询缺失预测。
    first_metrics = evaluate_query(first_gold, PredictionRecord(query_id="q1", papers=[_paper("10.1/a")]), [1])  # 构造满分查询。
    second_metrics = evaluate_query(second_gold, None, [1])  # 显式评测缺失预测。
    summary = aggregate_query_metrics([first_metrics, second_metrics], [1])  # 聚合两条查询。
    assert summary.predicted_query_count == 1  # 只记录一条实际预测。
    assert summary.cutoffs[1].macro_f1 == pytest.approx(0.5)  # 缺失查询按零参与宏平均。
    assert summary.cutoffs[1].micro_precision == pytest.approx(1.0)  # 唯一预测确实相关。
    assert summary.cutoffs[1].micro_recall == pytest.approx(0.5)  # 两个金标只命中一个。
