"""验证排序前本地论文过滤的年份、venue 和排除词规则。"""

from backend.app.models.paper import Paper  # 构造统一论文测试数据。
from backend.app.models.query import QuerySchema  # 构造结构化过滤约束。
from backend.app.services.filtering import filter_papers  # 导入待测本地过滤服务。


def test_filtering_applies_year_venue_and_exclude_constraints() -> None:
    """论文必须同时通过年份、venue 与排除词规则才能保留。"""
    papers = [  # 构造分别命中各类过滤规则的论文集合。
        Paper(paper_id="P1", title="Forecasting with Transformers", abstract="A practical method", year=2023, venue="NeurIPS 2023", source="openalex"),  # 应保留的论文。
        Paper(paper_id="P2", title="Future Forecasting", year=2025, venue="NeurIPS", source="openalex"),  # 超出年份范围。
        Paper(paper_id="P3", title="Forecasting with Transformers", year=2023, venue="ICML", source="openalex"),  # venue 不匹配。
        Paper(paper_id="P4", title="A Survey of Forecasting", year=2023, venue="NeurIPS", source="openalex"),  # 标题命中排除词。
        Paper(paper_id="P5", title="Forecasting", year=2023, venue="NeurIPS", abstract="This is a survey article", source="openalex"),  # 摘要命中排除词。
        Paper(paper_id="P6", title="Unknown Year Forecasting", venue="NeurIPS", source="openalex"),  # 指定年份时缺少年份。
    ]
    query = QuerySchema(  # 构造包含三类本地约束的查询。
        topic=["forecasting"],  # 提供合法搜索主题。
        year_range=(2022, 2024),  # 限制发表年份闭区间。
        venue=["  neurips  "],  # 验证 venue 的大小写和空白不影响匹配。
        exclude=["survey"],  # 排除标题或摘要包含的词。
    )
    filtered_papers = filter_papers(papers, query)  # 执行排序前的本地规则过滤。
    assert [paper.paper_id for paper in filtered_papers] == ["P1"]  # 验证仅保留通过全部约束的论文。


def test_filtering_preserves_input_without_optional_constraints() -> None:
    """未指定本地过滤条件时应保持论文顺序和完整性。"""
    papers = [  # 构造缺少年份和 venue 的边界数据。
        Paper(paper_id="P1", title="Paper One", source="openalex"),  # 提供部分元数据论文。
        Paper(paper_id="P2", title="Paper Two", source="openalex"),  # 提供第二篇论文以验证顺序。
    ]
    filtered_papers = filter_papers(papers, QuerySchema(topic=["paper"]))  # 使用没有可选过滤条件的合法查询。
    assert [paper.paper_id for paper in filtered_papers] == ["P1", "P2"]  # 验证不应因缺失可选元数据而过滤论文。
