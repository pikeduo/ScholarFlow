"""验证论文去重服务的标识优先级和回退策略。"""

from backend.app.models.paper import Paper  # 构造统一论文测试数据。
from backend.app.services.deduplication import deduplicate_papers  # 导入待测去重服务。


def test_deduplication_prioritizes_normalized_doi() -> None:
    """表示形式不同但核心相同的 DOI 应只保留首次论文。"""
    papers = [  # 构造两个来源不同但 DOI 相同的论文。
        Paper(paper_id="W1", title="论文 A", doi="https://doi.org/10.1000/ABC", source="openalex"),  # 使用 DOI URL 形式。
        Paper(paper_id="S1", title="论文 A 的不同标题", doi="doi:10.1000/abc", source="semantic_scholar"),  # 使用 DOI 前缀形式。
    ]
    deduplicated_papers = deduplicate_papers(papers)  # 执行 DOI 优先去重。
    assert [paper.paper_id for paper in deduplicated_papers] == ["W1"]  # 验证只保留首次 DOI 记录。


def test_deduplication_ignores_arxiv_version_suffix() -> None:
    """同一 arXiv 标识的不同版本应只保留首次论文。"""
    papers = [  # 构造没有 DOI 的预印本版本记录。
        Paper(paper_id="A1", title="论文 A", arxiv_id="arXiv:2501.00001v1", source="arxiv"),  # 提供第一版预印本。
        Paper(paper_id="A2", title="论文 A", arxiv_id="2501.00001v2", source="openalex"),  # 提供第二版预印本。
    ]
    deduplicated_papers = deduplicate_papers(papers)  # 执行 arXiv 回退去重。
    assert [paper.paper_id for paper in deduplicated_papers] == ["A1"]  # 验证版本后缀不会产生重复记录。


def test_deduplication_uses_source_id_before_title_fallback() -> None:
    """缺少稳定跨源标识时应先识别同源平台 ID，再使用标题回退键。"""
    papers = [  # 构造同源重复和跨源标题重复记录。
        Paper(paper_id="W1", title="同一论文", year=2025, source="openalex"),  # 提供首次同源记录。
        Paper(paper_id="W1", title="标题已修订", year=2025, source="openalex"),  # 提供相同平台 ID 记录。
        Paper(paper_id="S1", title="同一论文", year=2025, source="semantic_scholar"),  # 提供跨源标题回退重复记录。
    ]
    deduplicated_papers = deduplicate_papers(papers)  # 执行平台 ID 和标题回退去重。
    assert [paper.paper_id for paper in deduplicated_papers] == ["W1"]  # 验证两个后续重复记录均被过滤。
