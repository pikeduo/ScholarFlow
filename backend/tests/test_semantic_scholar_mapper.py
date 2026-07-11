"""验证 Semantic Scholar 论文响应的纯映射与来源溯源逻辑。"""

import json  # 加载本地 Semantic Scholar JSON fixture。
from pathlib import Path  # 定位测试 fixture 文件。

import pytest  # 提供映射异常断言工具。

from backend.app.adapters.semantic_scholar import SemanticScholarMappingError, map_semantic_scholar_paper  # 导入待测映射器和领域异常。


def _load_semantic_scholar_paper_fixture() -> dict[str, object]:
    """读取固定的 Semantic Scholar 论文响应样例。

    返回：
        dict[str, object]：不依赖网络的单条论文 fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "semantic_scholar_paper.json"  # 根据测试文件位置构造 fixture 路径。
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # 使用 UTF-8 解码并解析 JSON 数据。


def test_mapper_normalizes_semantic_scholar_paper_with_provenance() -> None:
    """映射器应保留跨来源标识、作者标识、开放链接和原始排名。"""
    paper = map_semantic_scholar_paper(_load_semantic_scholar_paper_fixture(), raw_rank=3)  # 映射固定 fixture 并传入来源原始排名。
    assert paper.paper_id == "S2-paper-123"  # 验证 Semantic Scholar 论文主标识映射。
    assert paper.semantic_scholar_id == "S2-paper-123"  # 验证来源标识被显式保留。
    assert paper.doi == "10.1000/semantic-scholar"  # 验证 DOI 映射。
    assert paper.arxiv_id == "2501.00001"  # 验证 arXiv 标识映射。
    assert paper.authors[0].source_author_ids["semantic_scholar"] == "S2-author-1"  # 验证来源作者标识映射。
    assert paper.source_records[0].raw_rank == 3  # 验证 RRF 所需的来源原始排名。
    assert paper.open_access_url == "https://example.org/paper.pdf"  # 验证来源提供的开放访问链接。
    assert paper.references == ["S2-reference-456"]  # 验证真实引用论文标识映射。


def test_mapper_rejects_semantic_scholar_paper_without_required_id() -> None:
    """缺少 Semantic Scholar paperId 时应返回可定位的映射错误。"""
    with pytest.raises(SemanticScholarMappingError, match="paperId"):  # 断言错误指出缺少必要来源标识。
        map_semantic_scholar_paper({"title": "缺少标识的论文"})  # 构造没有 paperId 的最小无效响应。
