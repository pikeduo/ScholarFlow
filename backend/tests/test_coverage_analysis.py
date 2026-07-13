"""验证覆盖缺口分析的约束优先级、停止条件和输入边界。"""

import pytest  # 提供公共输入边界的异常断言。

from backend.app.models.paper import PaperRecord  # 构造已完成核验的最终论文候选。
from backend.app.models.query_intent import QueryIntent  # 构造带有不同覆盖条件的搜索意图。
from backend.app.services.coverage_analysis import CoverageGapAnalyzer  # 导入待测的纯本地覆盖分析服务。


def _query(**overrides: object) -> QueryIntent:
    """构造可按用例覆盖的最小查询意图。"""
    payload: dict[str, object] = {  # 提供满足 QueryIntent 校验的默认字段。
        "original_query": "Transformer 在 ETT 上的预测",  # 保留用户原始查询用于完整契约。
        "normalized_query": "Transformer forecasting on ETT",  # 提供可执行的英文规范化查询。
        "query_language": "mixed",  # 标记中英文混合检索意图。
        "must_include": ["Transformer"],  # 默认要求明确的模型硬约束。
        "methods": ["Transformer"],  # 默认同时检查方法维度覆盖。
        "datasets": ["ETT"],  # 默认检查数据集维度覆盖。
        "target_paper_count": 2,  # 使用小目标数量让停止条件更清晰。
        "source_recall_count": 5,  # 保持来源召回规模大于最终目标。
    }
    payload.update(overrides)  # 允许各用例只声明差异字段。
    return QueryIntent(**payload)  # 返回经过 Pydantic 校验的查询意图。


def _paper(paper_id: str, *, status: str = "satisfied", title: str = "Transformer forecasting", abstract: str = "Evaluated on ETT benchmark.", year: int = 2024) -> PaperRecord:
    """构造带有公开元数据与可选核验状态的最终论文记录。"""
    return PaperRecord(paper_id=paper_id, title=title, abstract=abstract, year=year, source="openalex", constraint_status=status, llm_relevance_score=0.9)  # 让服务只依赖稳定的公开字段与核验状态。


def test_analyzer_reports_missing_dataset_and_result_count_then_recommends_continuation() -> None:
    """首轮关键数据集未覆盖且数量不足时应返回可执行的继续建议。"""
    report = CoverageGapAnalyzer().analyze(_query(), [_paper("paper-1", abstract="General forecasting benchmark.")], new_valid_count=1, source_counts={"openalex": 10, "semantic_scholar": 8})  # 构造方法满足但数据集未显式出现的一轮结果。

    assert report.high_relevance_count == 1  # 验证已核验论文被计入高相关数量。
    assert report.partial_relevance_count == 0  # 验证没有 uncertain 候选时部分相关数量为零。
    assert [(gap.gap_type, gap.constraint) for gap in report.gaps] == [("dataset", "ETT"), ("result_count", "高相关论文数量")]  # 验证先发现数据集缺口再报告数量不足。
    assert report.should_continue is True and report.stop_reason is None  # 验证首轮尚有缺口时仅建议控制器继续。


def test_analyzer_stops_when_target_and_key_constraints_are_covered() -> None:
    """目标数量与所有关键条件均覆盖时不应继续发起额外搜索。"""
    report = CoverageGapAnalyzer().analyze(_query(), [_paper("paper-1"), _paper("paper-2", title="Transformer model", abstract="ETT forecasting evaluation")], new_valid_count=2, source_counts={"openalex": 10, "semantic_scholar": 8})  # 构造两个均明确覆盖方法和数据集的候选。

    assert report.gaps == []  # 验证无需再生成任何补充查询聚焦。
    assert report.should_continue is False  # 验证完成目标后不会继续消耗预算。
    assert report.stop_reason == "已获得目标数量的高相关论文且关键约束已覆盖"  # 验证返回可展示的正常停止原因。


def test_analyzer_prioritizes_budget_and_reserves_the_final_gap_recovery_round() -> None:
    """预算触顶优先于其他判断，第二轮结果不足时必须保留第三轮补足机会。"""
    analyzer = CoverageGapAnalyzer()  # 复用默认最小新增高质量论文阈值。
    budget_report = analyzer.analyze(_query(), [], new_valid_count=0, source_counts={"openalex": 0}, budget_exhausted=True)  # 构造预算触顶且存在多个缺口的场景。
    recovery_report = analyzer.analyze(_query(), [], new_valid_count=0, source_counts={"openalex": 0}, current_round=2, max_rounds=3)  # 构造第二轮没有新增高质量论文但仍可切换第三来源的场景。
    no_gain_report = analyzer.analyze(_query(), [], new_valid_count=0, source_counts={"openalex": 0}, current_round=2, max_rounds=4)  # 构造在最终补足轮之前仍有额外轮次的低收益场景。

    assert budget_report.stop_reason == "搜索预算已达到上限"  # 验证预算保护优先于尝试补足缺口。
    assert recovery_report.should_continue is True and recovery_report.stop_reason is None  # 验证结果不足时不会因第二轮零增益而跳过第三轮补足。
    assert no_gain_report.stop_reason == "连续轮次新增高质量论文不足"  # 验证仍会在存在额外轮次时阻止连续低收益循环。
    assert budget_report.should_continue is False and no_gain_report.should_continue is False  # 验证预算和无效扩展均不会建议继续。


def test_analyzer_reports_source_failure_and_rejects_invalid_round_inputs() -> None:
    """来源不可用应形成来源缺口，非法轮次和新增数量必须在服务边界被拒绝。"""
    analyzer = CoverageGapAnalyzer()  # 构造待测服务实例。
    report = analyzer.analyze(_query(), [], new_valid_count=0, source_counts={"openalex": 0, "semantic_scholar": 3}, unavailable_sources=("openalex",))  # 构造单一来源失败但另一来源仍可工作的场景。

    assert any(gap.gap_type == "source" and gap.constraint == "openalex" and gap.severity == 0.7 for gap in report.gaps)  # 验证失败来源以更高严重度进入报告。
    with pytest.raises(ValueError, match="new_valid_count"):  # 断言新增数量不能为负数。
        analyzer.analyze(_query(), [], new_valid_count=-1, source_counts={})  # 传入无效新增数量。
    with pytest.raises(ValueError, match="current_round"):  # 断言轮次从一开始计数。
        analyzer.analyze(_query(), [], new_valid_count=0, source_counts={}, current_round=0)  # 传入无效首轮编号。
