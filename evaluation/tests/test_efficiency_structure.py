"""测试效率缺失值规则和确定性结构代理分。"""

import pytest  # 提供浮点近似断言。

from evaluation.contracts.common import ClassificationRecord, EvaluationPaper, EvaluationUsage, RelationRecord  # 构造 usage 与结构事实。
from evaluation.contracts.prediction import PredictionRecord  # 构造查询预测。
from evaluation.metrics.efficiency import summarize_efficiency  # 验证效率汇总。
from evaluation.metrics.structure import score_structure_query  # 验证结构代理分。


def test_efficiency_uses_complete_observations_and_derives_tokens() -> None:
    """完整输入输出 Token 可相加，全部组件存在时生成非官方代理分。"""
    predictions = [
        PredictionRecord(query_id="q1", usage=EvaluationUsage(academic_api_calls=2, actual_http_requests=3, llm_calls=1, input_tokens=100, output_tokens=20, latency_ms=1000, retry_count=1, rate_limit_count=0, cache_hit_count=2)),  # 使用分量 Token。
        PredictionRecord(query_id="q2", usage=EvaluationUsage(academic_api_calls=1, actual_http_requests=1, llm_calls=0, total_tokens=0, latency_ms=2000, retry_count=0, rate_limit_count=0, cache_hit_count=0)),  # 使用总 Token。
    ]
    summary = summarize_efficiency(predictions)  # 聚合完整观测。
    assert summary.academic_api_calls == 3  # API 调用完整求和。
    assert summary.total_tokens == 120  # 两种 Token 表达统一汇总。
    assert summary.latency_p95_ms == 2000  # 最近秩 P95 对两条样本取最大值。
    assert summary.proxy_score is not None  # 三个代理组件完整时可生成本地代理分。
    assert summary.proxy_label == "本地效率代理分（非官方）"  # 标签明确非官方。


def test_partial_efficiency_observation_stays_missing() -> None:
    """任一查询缺少效率字段时不得按零补齐或生成综合效率分。"""
    predictions = [PredictionRecord(query_id="q1", usage=EvaluationUsage(academic_api_calls=1, total_tokens=0, latency_ms=1000)), PredictionRecord(query_id="q2", usage=EvaluationUsage(total_tokens=0, latency_ms=1000))]  # 第二条缺少 API 调用数。
    summary = summarize_efficiency(predictions)  # 汇总部分缺失观测。
    assert summary.academic_api_calls is None  # 不报告不完整总和。
    assert summary.proxy_score is None  # 不生成看似完整的代理分。
    assert "academic_api_calls" in summary.missing_fields  # 明确报告缺失组件。


def test_structure_proxy_penalizes_duplicates_incomplete_fields_and_invalid_edges() -> None:
    """结构代理分应对重复、字段缺失和集合外关系确定性扣分。"""
    complete = EvaluationPaper(paper_id="a", doi="10.1/a", title="A", year=2024, authors=["Alice"], venue="V", source="openalex", relevance_score=0.9, recommendation_reason="Relevant")  # 构造完整论文。
    duplicate = complete.model_copy(update={"paper_id": "a-copy"})  # 构造相同 DOI 的重复论文。
    prediction = PredictionRecord(query_id="q1", papers=[complete, duplicate], relations=[RelationRecord(source="a", target="outside", type="cites")], classifications=[ClassificationRecord(paper_id="a", label="method")])  # 一条关系非法、一条分类合法。
    score = score_structure_query(prediction)  # 计算结构分解。
    assert score.ranked_list_legality == pytest.approx(0.5)  # 两条中一条重复。
    assert score.field_completeness == pytest.approx(1.0)  # 唯一保留论文八组字段完整。
    assert score.relation_legality == pytest.approx(0.5)  # 两条结构事实一条合法。
    assert score.proxy_score == pytest.approx(0.7)  # 按 0.4、0.4、0.2 固定权重合成。
