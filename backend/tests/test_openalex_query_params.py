"""验证结构化查询到 OpenAlex 参数的纯转换逻辑。"""

import pytest  # 提供异常断言工具。

from backend.app.adapters.openalex import OPENALEX_WORK_FIELDS, build_openalex_work_params  # 导入待测参数构造器。
from backend.app.models.query import QuerySchema  # 构造结构化查询测试数据。


def test_builder_converts_query_schema_to_openalex_params() -> None:
    """参数构造器应生成搜索词、年份过滤和最小字段选择。"""
    query = QuerySchema(  # 构造包含主要检索约束的查询。
        topic=["large language model"],  # 提供主题关键词。
        method=["forecasting"],  # 提供方法关键词。
        dataset=["ETT"],  # 提供数据集关键词。
        domain=["time series"],  # 提供领域关键词。
        must_include=["benchmark"],  # 提供必须包含关键词。
        exclude=["survey"],  # 提供留给本地后处理的排除词。
        venue=["NeurIPS"],  # 提供等待来源 ID 解析的期刊条件。
        year_range=(2022, 2025),  # 提供年份范围。
        target_count=30,  # 提供期望召回数量。
    )
    params = build_openalex_work_params(query)  # 构造不含密钥的 OpenAlex 参数。
    assert params["search"] == "large language model forecasting ETT time series benchmark"  # 验证关键词合并顺序。
    assert params["sort"] == "relevance_score:desc"  # 验证按 OpenAlex 排序指南使用显式降序相关性语法。
    assert params["filter"] == "publication_year:2022-2025"  # 验证年份范围转换。
    assert params["per_page"] == 30  # 验证目标数量映射为单页数量。
    assert params["select"] == ",".join(OPENALEX_WORK_FIELDS)  # 验证字段选择与映射器一致。
    assert "api_key" not in params  # 验证密钥只能由未来 HTTP 客户端注入。
    assert "survey" not in params["search"]  # 验证排除词不会被错误地作为正向搜索词。


def test_builder_rejects_query_without_search_terms() -> None:
    """缺少所有可搜索关键词时应拒绝构造无约束 API 请求。"""
    with pytest.raises(ValueError, match="至少需要一个"):  # 断言返回清晰的空查询错误。
        build_openalex_work_params(QuerySchema())  # 构造没有任何检索词的查询。
