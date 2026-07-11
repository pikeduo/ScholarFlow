"""验证结构化查询模型的默认值与约束校验。"""

import pytest  # 提供异常断言工具。
from pydantic import ValidationError  # 捕获 Pydantic 的输入校验异常。

from backend.app.models.query import QuerySchema  # 导入待测结构化查询模型。


def test_query_schema_accepts_planning_constraints() -> None:
    """结构化查询应保存项目规划书要求的全部主要约束。"""
    query = QuerySchema(  # 构造覆盖所有核心字段的有效查询。
        topic=["大语言模型"],  # 提供研究主题。
        method=["时间序列预测"],  # 提供研究方法。
        dataset=["ETT"],  # 提供目标数据集。
        domain=["人工智能"],  # 提供研究领域。
        year_range=(2022, 2026),  # 提供有效年份区间。
        venue=["NeurIPS"],  # 提供会议筛选条件。
        must_include=["forecasting"],  # 提供必须包含关键词。
        exclude=["survey"],  # 提供排除关键词。
        target_count=20,  # 使用默认目标规模对应的显式值。
    )
    assert query.topic == ["大语言模型"]  # 验证主题被原样保存。
    assert query.year_range == (2022, 2026)  # 验证年份区间通过校验。
    assert query.target_count == 20  # 验证目标数量被正确保存。


def test_query_schema_rejects_reversed_year_range() -> None:
    """年份起始值晚于结束值时应拒绝该查询。"""
    with pytest.raises(ValidationError, match="起始年份"):  # 断言返回可理解的年份错误。
        QuerySchema(topic=["forecasting"], year_range=(2026, 2022))  # 构造含有有效检索词的倒置年份区间。


def test_query_schema_rejects_conflicting_terms() -> None:
    """同一关键词不能同时作为必须包含和排除条件。"""
    with pytest.raises(ValidationError, match="不能包含相同关键词"):  # 断言返回可理解的冲突错误。
        QuerySchema(must_include=["LLM"], exclude=["llm"])  # 使用大小写不同的冲突关键词。


def test_query_schema_rejects_missing_effective_search_terms() -> None:
    """空查询或仅含空白词时应在模型层被拒绝。"""
    with pytest.raises(ValidationError, match="至少需要一个主题"):  # 断言完全空查询得到稳定错误。
        QuerySchema()  # 构造不包含任何检索意图字段的查询。
    with pytest.raises(ValidationError, match="至少需要一个主题"):  # 断言空白文本不能绕过搜索词约束。
        QuerySchema(topic=["  "], method=["\t"], dataset=[""])  # 构造仅包含空白搜索词的查询。
