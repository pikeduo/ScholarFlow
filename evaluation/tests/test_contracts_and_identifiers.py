"""测试离线评测参数边界和论文身份匹配规则。"""

import pytest  # 验证契约拒绝非法配置。
from pydantic import ValidationError  # 断言 Pydantic 跨字段错误。

from evaluation.contracts.common import EvaluationPaper  # 构造合成论文。
from evaluation.contracts.prediction import RankingConfig  # 验证候选数量语义。
from evaluation.metrics.identifiers import deduplicate_papers, normalize_arxiv_id, normalize_doi, papers_match  # 验证规范化和匹配。


def test_identifier_normalization_and_deduplication() -> None:
    """DOI 与 arXiv 展示格式差异应稳定归一并去重。"""
    assert normalize_doi("https://doi.org/10.1000/ABC.1).") == "10.1000/abc.1"  # 移除 URL、大小写和末尾引用标点。
    assert normalize_arxiv_id("https://arxiv.org/pdf/2401.12345v3.pdf") == "2401.12345"  # 移除 PDF 后缀和版本号。
    papers = [EvaluationPaper(doi="10.1000/abc.1", title="First"), EvaluationPaper(doi="DOI:10.1000/ABC.1", title="Duplicate")]  # 构造同一 DOI 的两条记录。
    unique, duplicate_count = deduplicate_papers(papers)  # 执行保序去重。
    assert len(unique) == 1  # 只保留首次出现记录。
    assert duplicate_count == 1  # 重复计数可用于结构评分和报告。


def test_conflicting_strong_identifier_blocks_title_fallback() -> None:
    """同字段强标识冲突时不得仅因标题相同而误合并。"""
    left = EvaluationPaper(doi="10.1000/left", title="Same Paper", year=2024, authors=["Alice"] )  # 构造左侧 DOI。
    right = EvaluationPaper(doi="10.1000/right", title="Same Paper", year=2024, authors=["Alice"] )  # 构造冲突 DOI。
    assert papers_match(left, right) is False  # 强标识冲突优先于标题回退。


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (EvaluationPaper(pmid="12345678"), EvaluationPaper(pmid="PMID:12345678")),  # 覆盖 PMID 文本前缀。
        (EvaluationPaper(openalex_id="W123"), EvaluationPaper(openalex_id="https://openalex.org/W123/")),  # 覆盖 OpenAlex URL。
        (EvaluationPaper(semantic_scholar_id="abcdef"), EvaluationPaper(semantic_scholar_id="https://www.semanticscholar.org/paper/example-title/ABCDEF")),  # 覆盖 Semantic Scholar slug URL。
        (EvaluationPaper(dblp_key="conf/test/Paper24"), EvaluationPaper(dblp_key="https://dblp.org/rec/conf/test/Paper24.html")),  # 覆盖 DBLP URL。
    ],
)
def test_platform_identifier_formats_match(left: EvaluationPaper, right: EvaluationPaper) -> None:
    """PMID 与三类平台标识的 URL/文本形式应稳定匹配。"""
    assert papers_match(left, right) is True  # 平台展示格式不能改变论文身份。


def test_title_year_author_fallback_ignores_different_fixture_ids() -> None:
    """跨 fixture 内部 ID 不同时仍可按标题、年份和作者回退匹配。"""
    left = EvaluationPaper(paper_id="gold-1", title="A Study: On Retrieval", year=2024, authors=["Alice Zhang"])  # 构造金标内部 ID。
    right = EvaluationPaper(paper_id="prediction-9", title="A Study on Retrieval", year=2024, authors=["ALICE ZHANG"])  # 构造预测内部 ID。
    assert papers_match(left, right) is True  # 标点和大小写差异不影响保守回退。


def test_ranking_config_only_checks_enabled_stages() -> None:
    """关闭的排序阶段不得限制最终数量，启用后必须遵守候选边界。"""
    config = RankingConfig(semantic_top_k=5, cross_encoder_top_k=4, target_paper_count=20)  # 两个本地模型均关闭。
    assert config.target_paper_count == 20  # 关闭阶段不错误限制最终目标。
    with pytest.raises(ValidationError, match="target_paper_count 不能大于 semantic_top_k"):  # 只启用 BGE-M3 时目标受其保留量约束。
        RankingConfig(semantic_ranking_enabled=True, semantic_top_k=5, cross_encoder_ranking_enabled=False, target_paper_count=6)
    with pytest.raises(ValidationError, match="cross_encoder_top_k 不能大于 semantic_top_k"):  # 两级都启用时 Cross Encoder 输入不能超过 BGE-M3 输出。
        RankingConfig(semantic_ranking_enabled=True, semantic_top_k=10, cross_encoder_ranking_enabled=True, cross_encoder_top_k=11, target_paper_count=5)


def test_evaluation_top_k_is_independent_and_normalized() -> None:
    """评分 Top-K 应独立于候选规模并自动去重排序。"""
    config = RankingConfig(source_recall_count=1, target_paper_count=1, evaluation_top_k=[20, 5, 5, 10])  # 使用彼此不同的数量参数。
    assert config.evaluation_top_k == [5, 10, 20]  # 评分截断只规范化自身。
