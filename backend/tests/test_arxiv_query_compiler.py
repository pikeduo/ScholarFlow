"""验证 arXiv 仅按研究主题执行宽松来源召回。"""

from backend.app.adapters.arxiv import compile_arxiv_search_query, select_arxiv_concepts  # 导入不访问网络或模型的 arXiv 查询编译边界。
from backend.app.models.query_intent import QueryIntent  # 构造完整但不会被编译器修改的统一查询意图。


def _query(**overrides: object) -> QueryIntent:
    """构造允许单个用例覆盖字段的最小有效 QueryIntent。"""
    values: dict[str, object] = {  # 提供不会读取真实环境或模型的稳定默认值。
        "original_query": "检索相关论文",  # 满足领域契约但不会进入 arXiv 原始语法。
        "normalized_query": "time series forecasting",  # 为主题缺失场景提供英文回退。
        "query_language": "en",  # 标记规范化查询语言。
        "target_paper_count": 20,  # 验证来源单页参数保持现有上限逻辑。
    }
    values.update(overrides)  # 允许每个用例只声明自身关心的输入。
    return QueryIntent(**values)  # 交由领域模型校验测试对象。


def test_compiler_uses_only_research_topics_with_or_and_keeps_year_range() -> None:
    """两个研究主题及其有限变体应形成一个 OR 组，并只与年份使用 AND。"""
    query = _query(research_topics=["time series foundation models", "large language models for forecasting"], methods=["pretrained models"], tasks=["zero-shot forecasting"], datasets=["ETT"], must_include=["cross-domain generalization"], should_include=["open source"], exclude=["survey"], paper_types=["conference"], year_range=(2022, 2026))  # 构造含完整后续约束的来源召回输入。
    original_dump = query.model_dump()  # 保存编译前快照以验证不修改 QueryIntent。

    compiled = compile_arxiv_search_query(query)  # 仅生成 arXiv 前置召回表达。

    assert 'all:"time series foundation model"' in compiled and 'all:"time series foundation models"' in compiled  # 验证有限单复数变体进入同一宽松主题组。
    assert 'all:"large language models for forecasting"' in compiled and 'all:"LLM forecasting"' in compiled  # 验证完整主题与明确缩写变体均可召回。
    assert compiled.count(" OR ") >= 3 and compiled.count(" AND ") == 1  # 验证主题之间仅 OR，且仅主题组与年份条件使用 AND。
    assert "submittedDate:[202201010000 TO 202612312359]" in compiled  # 验证年份仍由 arXiv 投稿日期范围近似过滤。
    assert all(term not in compiled for term in ["pretrained", "zero-shot", "ETT", "cross-domain", "open source", "survey", "conference"])  # 验证方法、任务、数据集、硬软约束、排除和论文类型均不进入来源查询。
    assert query.model_dump() == original_dump  # 验证宽召回不修改后续过滤、排序和核验仍需使用的完整意图。


def test_compiler_limits_to_two_research_topics_and_falls_back_only_when_topics_missing() -> None:
    """arXiv 最多使用两个主题；仅没有有效主题时回退规范化查询。"""
    topic_query = _query(research_topics=["graph neural networks", "traffic forecasting", "spatiotemporal prediction"], methods=["Transformer"])  # 构造超过来源级上限的主题列表。
    fallback_query = _query(research_topics=["  "], normalized_query="recent graph neural networks for traffic forecasting with zero-shot evaluation", methods=["Transformer"])  # 构造研究主题无效的回退场景。

    topic_compiled = compile_arxiv_search_query(topic_query)  # 编译超过两个主题的来源查询。
    fallback_compiled = compile_arxiv_search_query(fallback_query)  # 编译主题缺失时的规范化查询回退。

    assert select_arxiv_concepts(topic_query) == ["graph neural networks", "traffic forecasting"]  # 验证第三主题不会扩展为额外来源强条件。
    assert "spatiotemporal prediction" not in topic_compiled and "Transformer" not in topic_compiled  # 验证第三主题和方法均未进入来源查询。
    assert 'all:"graph neural networks"' in fallback_compiled and 'all:"traffic forecasting"' in fallback_compiled  # 验证回退查询被压缩为短概念而非整句精确短语。
    assert "recent graph neural networks for traffic forecasting with zero-shot evaluation" not in fallback_compiled and "Transformer" not in fallback_compiled  # 验证长自然语言整句和方法均不会逃逸进 arXiv 查询。


def test_compiler_sanitizes_user_arxiv_syntax_deterministically_without_other_fields() -> None:
    """用户 arXiv 语法只能作为主题文本被清理，不能改变编译结构。"""
    injected = '"time series" OR all:transformer) AND (cat:cs.LG'  # 构造带字段前缀与布尔语法的误用输入。
    query = _query(research_topics=[injected], methods=["unrelated method"], tasks=["unrelated task"])  # 确保非主题字段不会影响来源编译。

    first_compiled = compile_arxiv_search_query(query)  # 第一次生成纯字符串结果。
    second_compiled = compile_arxiv_search_query(query)  # 第二次生成验证无随机状态。

    assert first_compiled == second_compiled  # 验证相同意图得到完全确定的来源查询。
    assert "all:transformer" not in first_compiled and "cat:cs" not in first_compiled and "unrelated" not in first_compiled  # 验证用户字段语法和非主题字段均不会进入编译结构。
