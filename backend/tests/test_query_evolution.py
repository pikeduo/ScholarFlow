"""验证查询演化只修复覆盖缺口、保持硬约束并拒绝重复查询。"""

import pytest  # 提供服务配置边界的异常断言。

from backend.app.models.coverage import CoverageGap, CoverageReport  # 构造无需真实检索结果的覆盖缺口报告。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 构造当前查询和已有子查询。
from backend.app.services.query_evolution import QueryEvolutionService  # 导入待测的纯本地查询演化服务。


def _query() -> QueryIntent:
    """构造带有不可放宽条件和已有查询计划的测试意图。"""
    return QueryIntent(  # 返回满足领域校验的最小完整查询意图。
        original_query="查找 ETT 上的 Transformer 预测论文",  # 保留用户原始查询供契约完整性验证。
        normalized_query="time series forecasting",  # 提供尚未包含数据集和方法的英文基础查询。
        query_language="mixed",  # 标记中英文混合查询。
        research_topics=["time series forecasting"],  # 提供可用于补充查询的研究主题。
        methods=["Transformer"],  # 提供方法缺口语境。
        datasets=["ETT"],  # 提供数据集缺口语境。
        must_include=["forecasting"],  # 提供必须原样保留的硬约束。
        exclude=["survey"],  # 提供必须原样保留的排除条件。
        subqueries=[QuerySubquery(query="time series forecasting benchmark", language="en", purpose="citation")],  # 提供已有但尚未重复的数据查询。
    )


def _report(*gaps: CoverageGap) -> CoverageReport:
    """构造按测试需要注入缺口的最小覆盖报告。"""
    return CoverageReport(  # 返回无需模型、来源或预算的稳定分析结果。
        target_count=20,
        gaps=list(gaps),
        new_valid_count=1,
        marginal_gain=0.05,
        should_continue=True,
    )


def test_evolution_generates_query_only_for_queryable_gap_and_preserves_hard_constraints() -> None:
    """数据集缺口应生成英文子查询，来源和数量缺口不应伪造检索词。"""
    query = _query()  # 构造含硬约束和已有子查询的原始意图。
    result = QueryEvolutionService().evolve(  # 执行不访问模型或外部 API 的确定性演化。
        query,
        _report(
            CoverageGap(gap_type="dataset", constraint="ETT", severity=0.9, current_match_count=0, recommended_query_focus="ETT"),  # 提供可通过文本检索修复的数据集缺口。
            CoverageGap(gap_type="source", constraint="semantic_scholar", severity=0.7, current_match_count=0, recommended_query_focus="semantic_scholar"),  # 提供应由路由器处理的来源缺口。
            CoverageGap(gap_type="result_count", constraint="高相关论文数量", severity=0.5, current_match_count=3, recommended_query_focus="高相关论文数量"),  # 提供不能直接转为关键词的数量缺口。
        ),
    )

    assert [subquery.query for subquery in result.generated_subqueries] == ["time series forecasting Transformer ETT"]  # 验证只追加针对 ETT 的确定性英文补充查询。
    assert result.generated_subqueries[0].purpose == "dataset"  # 验证数据集缺口使用合法的 dataset 子查询用途。
    assert result.query_intent.must_include == query.must_include and result.query_intent.exclude == query.exclude  # 验证演化绝不放宽或改写硬约束与排除词。
    assert result.query_intent.subqueries[-1] == result.generated_subqueries[0]  # 验证更新后的意图可直接交给后续控制器执行。
    assert result.skipped_gap_count == 2  # 验证来源和数量缺口被明确保留给后续控制器处理。


def test_evolution_rejects_executed_or_similar_subquery() -> None:
    """候选与已执行或已有子查询相同、过度相似时不得再次生成。"""
    result = QueryEvolutionService().evolve(  # 传入与预计数据集候选相同的已执行子查询。
        _query(),
        _report(CoverageGap(gap_type="dataset", constraint="ETT", severity=0.9, current_match_count=0, recommended_query_focus="ETT")),  # 提供会生成 ETT 补充表达的缺口。
        executed_subqueries=["ETT time series forecasting Transformer"],  # 使用不同词序验证指纹和相似度均能拦截。
    )

    assert result.generated_subqueries == []  # 验证不会重复发起等价来源调用。
    assert result.skipped_gap_count == 1  # 验证重复缺口被计入跳过统计。
    assert result.warnings == ["缺口“ETT”没有可执行的新查询"]  # 验证控制器获得稳定且可展示的停止依据。


def test_evolution_uses_method_purpose_and_rejects_invalid_configuration() -> None:
    """方法缺口应生成 method 子查询，非法生成上限和相似度阈值必须被拒绝。"""
    result = QueryEvolutionService().evolve(  # 执行针对方法缺口的确定性演化。
        _query(),
        _report(CoverageGap(gap_type="method", constraint="Transformer", severity=0.8, current_match_count=0, recommended_query_focus="Transformer")),  # 提供可查询的方法缺口。
    )

    assert result.generated_subqueries[0].purpose == "method"  # 验证方法缺口映射到合法 method 用途。
    with pytest.raises(ValueError, match="max_queries_per_gap"):  # 断言零生成预算在服务装配时被拒绝。
        QueryEvolutionService(max_queries_per_gap=0)  # 构造无效配置。
    with pytest.raises(ValueError, match="similarity_threshold"):  # 断言越界相似度阈值被拒绝。
        QueryEvolutionService(similarity_threshold=0.0)  # 构造无效配置。
