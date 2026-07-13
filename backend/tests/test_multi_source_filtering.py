"""验证多源融合论文按 QueryIntent 执行的确定性规则过滤。"""

from backend.app.models.paper import PaperAuthor, PaperRecord  # 构造带作者和机构信息的融合论文。
from backend.app.models.query_intent import QueryIntent  # 构造完整硬约束查询意图。
from backend.app.services.multi_source_filtering import MultiSourcePaperFilter  # 导入待测的多源规则过滤服务。


def _paper(paper_id: str, **overrides: object) -> PaperRecord:
    """构造可由单项覆盖验证过滤原因的最小融合论文。"""
    paper_data: dict[str, object] = {  # 先集中声明默认字段，避免与覆盖字段重复传参。
        "paper_id": paper_id,  # 提供稳定测试标识。
        "title": "Transformer Forecasting",  # 提供必须词命中的默认标题。
        "abstract": "Evaluation on benchmark data.",  # 提供可公开匹配的默认摘要。
        "authors": [PaperAuthor(name="Ada Lovelace", institution="Scholar Lab")],  # 提供默认匹配的作者和机构。
        "year": 2024,  # 提供默认匹配的年份。
        "venue": "NeurIPS",  # 提供默认匹配的 venue。
        "paper_type": "conference",  # 提供默认匹配的论文类型。
        "source": "openalex",  # 标记统一来源模型要求的来源。
        "work_family_id": f"work-{paper_id}",  # 提供可统计的版本族标识。
    }
    paper_data.update(overrides)  # 用当前用例字段覆盖默认值，确保每个关键字仅出现一次。
    return PaperRecord(**paper_data)  # 将合并后的单一字段映射传给 Pydantic 模型。


def _query() -> QueryIntent:
    """构造同时覆盖所有当前可验证硬约束的查询意图。"""
    return QueryIntent(  # 构造不依赖网络或 LLM 的完整筛选条件。
        original_query="NeurIPS 的 Transformer 预测论文",  # 提供用户原始查询。
        normalized_query="Transformer forecasting NeurIPS",  # 提供可复现规范化查询。
        query_language="en",  # 标记查询语言。
        year_range=(2023, 2025),  # 限制发表年份闭区间。
        paper_types=["conference"],  # 限制会议论文类型。
        venues=["NeurIPS"],  # 限制目标会议。
        authors=["Ada Lovelace"],  # 限制目标作者。
        institutions=["Scholar Lab"],  # 限制目标机构。
        must_include=["transformer"],  # 要求标题、摘要或关键词包含方法词。
        exclude=["survey"],  # 排除综述主题。
    )


def test_filter_keeps_paper_type_mismatch_for_relevance_ranking() -> None:
    """类型不匹配论文必须保留，让后续相关性排序而非元数据类型决定名次。"""
    papers = [  # 构造一篇匹配论文和分别违反一项规则的候选论文。
        _paper("keep"),  # 保留全部通过约束的论文。
        _paper("year", year=2020),  # 违反年份范围。
        _paper("type", paper_type="article"),  # 类型不匹配但仍应进入后续相关性排序。
        _paper("venue", venue="ICML"),  # 违反 venue。
        _paper("author", authors=[PaperAuthor(name="Grace Hopper", institution="Scholar Lab")]),  # 违反作者约束。
        _paper("institution", authors=[PaperAuthor(name="Ada Lovelace", institution="Other Lab")]),  # 违反机构约束。
        _paper("must", title="Forecasting", abstract="Benchmark evaluation."),  # 缺少必须词。
        _paper("exclude", title="Transformer survey", abstract="Benchmark evaluation."),  # 命中排除词。
    ]

    result = MultiSourcePaperFilter().filter(papers, _query())  # 执行确定性规则过滤。

    assert [paper.paper_id for paper in result.papers] == ["keep", "type"]  # 验证论文类型不匹配不会被提前过滤。
    assert result.input_count == 8  # 验证输入候选统计正确。
    assert result.filtered_count == 6  # 验证仅确定性硬约束失败候选被移除。
    assert result.filter_reason_counts == {"year_range": 1, "venue": 1, "author": 1, "institution": 1, "must_include": 1, "exclude": 1}  # 验证论文类型不再作为移除原因。
    assert result.work_family_count == 2  # 验证版本族统计包含保留的类型不匹配候选。


def test_filter_keeps_all_papers_when_query_has_no_filterable_hard_constraints() -> None:
    """没有可验证硬约束时，过滤器不应因为来源可选字段缺失而移除论文。"""
    query = QueryIntent(  # 构造没有年份、类型、文本或元数据硬约束的最小意图。
        original_query="探索性查询",  # 提供合法原始查询。
        normalized_query="exploratory query",  # 提供合法规范化查询。
        query_language="mixed",  # 标记混合查询语言。
    )
    papers = [PaperRecord(paper_id="minimal", title="Minimal Paper", source="openalex")]  # 构造缺少可选元数据的融合候选。

    result = MultiSourcePaperFilter().filter(papers, query)  # 执行无硬约束的过滤。

    assert [paper.paper_id for paper in result.papers] == ["minimal"]  # 验证缺失可选元数据不会被错误过滤。
    assert result.filtered_count == 0  # 验证没有论文被移除。
    assert result.filter_reason_counts == {}  # 验证没有失败原因统计。


def test_filter_keeps_unknown_optional_metadata_for_later_verification() -> None:
    """来源缺少类型、venue、作者或机构时不应在语义核验前制造假阴性。"""
    query = QueryIntent(  # 构造需要来源元数据核验的明确约束。
        original_query="Scholar Lab 在 NeurIPS 发表的会议论文",  # 提供用户原始查询。
        normalized_query="Scholar Lab NeurIPS conference paper",  # 提供规范化检索文本。
        query_language="mixed",  # 标记中英混合查询。
        paper_types=["conference"],  # 指定论文类型。
        venues=["NeurIPS"],  # 指定 venue。
        authors=["Ada Lovelace"],  # 指定作者。
        institutions=["Scholar Lab"],  # 指定机构。
    )
    paper = PaperRecord(paper_id="unknown-metadata", title="Potentially Relevant Paper", source="openalex")  # 构造来源可选元数据均缺失的论文。

    result = MultiSourcePaperFilter().filter([paper], query)  # 执行确定性过滤。

    assert [candidate.paper_id for candidate in result.papers] == ["unknown-metadata"]  # 验证未知不等同于明确不满足。
    assert result.filter_reason_counts == {}  # 验证候选留给 BGE-M3 和 LLM 后续核验。
