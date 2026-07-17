"""验证 arXiv 专属查询编译器不会将结构化意图收窄为全部精确短语。"""

from backend.app.adapters.arxiv import build_arxiv_search_params, compile_arxiv_search_query, select_arxiv_concepts  # 导入只含纯字符串处理的 arXiv 编译边界。
from backend.app.models.query_intent import QueryIntent  # 构造不需要网络或模型的统一查询意图。


def _query(**overrides: object) -> QueryIntent:
    """构造允许单个用例覆盖字段的最小有效 QueryIntent。"""
    values: dict[str, object] = {  # 提供能够落入 arXiv 适配器的稳定默认意图。
        "original_query": "检索相关论文",  # 满足领域契约但不会进入 arXiv 原始语法。
        "normalized_query": "time series forecasting",  # 提供结构化字段缺失时的英文回退。
        "query_language": "en",  # 标记规范化查询为英文。
        "target_paper_count": 20,  # 验证默认来源上限回退保持不变。
    }
    values.update(overrides)  # 允许每个测试只声明与断言相关的输入差异。
    return QueryIntent(**values)  # 由领域模型继续校验测试输入。


def test_compiler_uses_two_distinct_concept_groups_with_alias_or_variants() -> None:
    """主题和方法应形成两个 AND 组，方法组内部保留有限别名。"""
    query = _query(research_topics=["time series forecasting"], methods=["large language models"], tasks=["zero-shot forecasting"], datasets=["ETT"], must_include=["multivariate"], year_range=(2024, 2026))  # 提供超过两个字段的典型结构化意图。
    original_dump = query.model_dump()  # 在编译前保留完整 QueryIntent 快照。
    compiled = compile_arxiv_search_query(query)  # 仅生成 arXiv 前置召回表达。
    assert '(all:"time series forecasting" OR all:"time-series forecasting")' in compiled  # 验证主题组使用连字符变体和 OR。
    assert '(all:"large language model" OR all:"large language models" OR all:"LLM")' in compiled  # 验证方法组使用明确别名和 OR。
    assert " AND " in compiled and "submittedDate:[202401010000 TO 202612312359]" in compiled  # 验证概念组间 AND 与年份过滤仍保留。
    assert "zero-shot forecasting" not in compiled and 'all:"ETT"' not in compiled and 'all:"multivariate"' not in compiled  # 验证次要约束不会在已有两个核心概念时继续收窄 arXiv 召回。
    assert query.model_dump() == original_dump and query.tasks == ["zero-shot forecasting"] and query.datasets == ["ETT"] and query.must_include == ["multivariate"]  # 验证未进入 arXiv 的约束仍留在原始意图供后续过滤、排序和核验使用。


def test_compiler_limits_to_two_groups_and_replaces_overlapping_concepts() -> None:
    """高度重叠的主题不应占用两个 AND 条件，信息更完整者应被保留。"""
    concepts = select_arxiv_concepts(_query(research_topics=["time series forecasting", "multivariate time series forecasting"], methods=["graph neural networks"], tasks=["zero-shot forecasting"]))  # 提供包含关系主题和两个后续独立候选。
    assert concepts == ["multivariate time series forecasting", "graph neural networks"]  # 验证更完整主题替换旧主题，并仅保留两个独立概念。


def test_compiler_uses_single_concept_and_keeps_paging_and_sort_params() -> None:
    """只有一个有效概念时不得凑第二组，既有分页和排序参数保持不变。"""
    params = build_arxiv_search_params(_query(research_topics=["  Time Series Forecasting  "], source_recall_count=37))  # 构造只有主题且显式来源召回规模的意图。
    assert params["search_query"] == '(all:"Time Series Forecasting" OR all:"Time-Series Forecasting")'  # 验证单概念仅生成一个 OR 组。
    assert params["start"] == 0 and params["max_results"] == 37  # 验证页码起点与来源召回上限未被编译器改变。
    assert params["sortBy"] == "relevance" and params["sortOrder"] == "descending"  # 验证来源排序参数维持原契约。


def test_compiler_splits_long_subquery_without_preserving_the_full_exact_phrase() -> None:
    """多轮补充句子进入 research_topics 时必须拆为两个短核心概念。"""
    long_subquery = "recent large language models for multivariate time series forecasting with zero-shot evaluation"  # 复现多轮控制器写入 research_topics 的完整英文子查询形状。
    compiled = compile_arxiv_search_query(_query(normalized_query=long_subquery, research_topics=[long_subquery]))  # 仅通过纯函数编译长子查询，不调用控制器或网络。
    assert long_subquery not in compiled  # 禁止将整句包装为 all:"完整长句"。
    assert 'all:"large language models"' in compiled and 'all:"multivariate time series forecasting"' in compiled  # 验证出现顺序中的两个短核心概念被保留。
    assert compiled.count(" AND ") == 1  # 验证没有把第三个细粒度任务继续加入强制条件。


def test_compiler_sanitizes_user_arxiv_syntax_and_is_deterministic() -> None:
    """引号、字段前缀、布尔词和括号只能作为文本被清理，不能改变编译结构。"""
    injected = '"time series" OR all:transformer) AND (cat:cs.LG'  # 构造包含用户提供 arXiv 原始语法的恶意或误用输入。
    query = _query(normalized_query=injected, research_topics=[injected])  # 同时覆盖回退字段，避免额外正常概念干扰语法边界断言。
    first_compiled = compile_arxiv_search_query(query)  # 第一次编译纯字符串结果。
    second_compiled = compile_arxiv_search_query(query)  # 第二次编译验证无随机状态。
    assert first_compiled == second_compiled  # 相同意图必须得到完全一致的来源查询。
    assert "OR all:transformer" not in first_compiled and "all:transformer" not in first_compiled and "cat:cs" not in first_compiled  # 用户原始语法不能逃逸到编译器生成的布尔或字段结构。
    assert 'all:"time series transformer cs LG"' in first_compiled  # 清理后仍保留可审计的普通文本词项。


def test_compiler_falls_back_to_and_splits_a_long_normalized_query_without_mutating_intent() -> None:
    """结构化字段为空时应回退规范化查询，且不会修改原始意图中的后续约束。"""
    normalized_query = "recent graph neural networks for traffic forecasting with zero-shot evaluation"  # 构造没有结构化字段的长规范化表达。
    query = _query(normalized_query=normalized_query)  # 保持全部结构化字段为空以触发规范化查询回退。
    original_dump = query.model_dump()  # 在编译前快照完整领域意图。
    compiled = compile_arxiv_search_query(query)  # 只生成 arXiv 前置召回表达。
    assert 'all:"graph neural networks"' in compiled and 'all:"traffic forecasting"' in compiled  # 验证长回退查询同样被拆为两个短概念。
    assert query.model_dump() == original_dump  # 验证编译器不放宽或修改整体 QueryIntent。


def test_compiler_ignores_blank_and_case_duplicate_candidates() -> None:
    """空白值和大小写等价概念不得形成额外 OR 或 AND 子句。"""
    concepts = select_arxiv_concepts(_query(research_topics=["", "  ", "Time Series Forecasting", "time series forecasting"], methods=["  "]))  # 构造空白和大小写重复的字段值。
    assert concepts == ["Time Series Forecasting"]  # 验证仅保留首次有效展示形式。
