"""验证论文与查询文本构造、缺失元数据边界及模型相关哈希。"""

import pytest  # 提供标题为空时的稳定异常断言。

from backend.app.models.paper import PaperRecord  # 构造已规范化论文输入。
from backend.app.models.query_intent import QueryIntent  # 构造结构化查询输入。
from backend.app.services.paper_text import PAPER_EMBEDDING_TEXT_VERSION, PAPER_RERANKER_TEXT_VERSION, QUERY_TEXT_VERSION, PaperTextBuilder, PaperTextBuilderError  # 导入待测构造器与公开版本常量。


def _paper(**changes: object) -> PaperRecord:
    """构造包含代表性公开元数据的论文，允许测试覆盖边界字段。"""
    values: dict[str, object] = {"paper_id": "paper-1", "title": "  Cross-lingual\nPaper Search  ", "abstract": "  A semantic\tsearch benchmark.  ", "keywords": ["retrieval", "  multilingual "], "venue": "  ACL  ", "year": 2025, "source": "openalex"}  # 提供可被各测试按需覆盖的最小完整记录。
    values.update(changes)  # 允许各测试只声明当前关注的差异。
    return PaperRecord.model_validate(values)  # 经过领域模型校验后返回论文记录。


def _query(**changes: object) -> QueryIntent:
    """构造带硬、软和排除约束的查询意图。"""
    values: dict[str, object] = {"original_query": "多语言论文检索", "normalized_query": "cross-lingual paper search", "query_language": "mixed", "methods": ["dense retrieval"], "datasets": ["MIRACL"], "tasks": ["paper search"], "must_include": ["multilingual"], "should_include": ["efficient"], "exclude": ["web pages"]}  # 提供可验证字段顺序的完整结构化查询。
    values.update(changes)  # 允许边界测试替换单个查询字段。
    return QueryIntent.model_validate(values)  # 经过跨字段约束校验后返回查询意图。


def test_build_embedding_text_keeps_title_and_uses_stable_field_order() -> None:
    """嵌入文本应规范化空白、固定字段顺序并记录文本格式版本。"""
    result = PaperTextBuilder().build_embedding_text(_paper())  # 使用默认 BGE-M3 模型身份构造论文向量文本。

    assert result.text == "Title: Cross-lingual Paper Search\nKeywords: retrieval, multilingual\nAbstract: A semantic search benchmark.\nVenue: ACL\nYear: 2025"  # 验证只选用规划规定的公开字段。
    assert result.builder_version == PAPER_EMBEDDING_TEXT_VERSION  # 验证调用方可据此识别文本格式。
    assert len(result.text_hash) == 64  # 验证生成 SHA-256 十六进制摘要。


def test_build_embedding_text_supports_missing_abstract_without_losing_title() -> None:
    """缺摘要论文仍应以标题、关键词、venue 和年份生成可索引文本。"""
    result = PaperTextBuilder().build_embedding_text(_paper(abstract="", keywords=[], venue=None, year=None))  # 构造来源缺少多数补充字段的论文。

    assert result.text == "Title: Cross-lingual Paper Search\nKeywords: \nAbstract: \nVenue: Unknown\nYear: Unknown"  # 验证标题不被缺失摘要场景吞掉。


def test_text_hash_changes_with_model_or_text_and_stays_stable_for_same_input() -> None:
    """模型身份和语义文本变化均必须触发向量缓存失效。"""
    paper = _paper()  # 构造同一论文用于比较哈希。
    default_builder = PaperTextBuilder()  # 使用默认模型身份。
    same_hash = default_builder.build_embedding_text(paper).text_hash  # 首次构造基线哈希。

    assert same_hash == default_builder.build_embedding_text(paper).text_hash  # 验证同一输入跨调用稳定。
    assert same_hash != PaperTextBuilder(embedding_model_revision="rev-2").build_embedding_text(paper).text_hash  # 验证权重修订变化会失效旧向量。
    assert same_hash != default_builder.build_embedding_text(_paper(abstract="Different abstract")).text_hash  # 验证摘要变化会失效旧向量。


def test_build_reranker_and_query_text_follow_their_own_versions() -> None:
    """精排与查询文本应采用独立格式版本，并让硬条件出现在软条件之前。"""
    builder = PaperTextBuilder()  # 复用同一构造器以验证不同文本视图。
    reranker_result = builder.build_reranker_text(_paper())  # 构造精简论文侧输入。
    query_result = builder.build_query_text(_query())  # 构造结构化查询输入。

    assert reranker_result.text == "Cross-lingual Paper Search\nA semantic search benchmark."  # 验证精排不混入 venue、年份等默认字段。
    assert reranker_result.builder_version == PAPER_RERANKER_TEXT_VERSION  # 验证精排文本可独立演进。
    assert query_result.builder_version == QUERY_TEXT_VERSION  # 验证查询文本可独立演进。
    assert query_result.text.index("Must include: multilingual") < query_result.text.index("Should include: efficient")  # 验证硬约束优先于软偏好。
    assert query_result.text.index("Should include: efficient") < query_result.text.index("Exclude: web pages")  # 验证排除说明位于偏好之后。


def test_build_text_rejects_blank_title() -> None:
    """仅空白标题不可生成可解释向量，应返回稳定业务错误。"""
    with pytest.raises(PaperTextBuilderError, match="论文标题不能为空"):  # 断言不泄露底层模型或来源错误。
        PaperTextBuilder().build_embedding_text(_paper(title=" \t "))  # 模拟历史或异常来源提供的空白标题。
