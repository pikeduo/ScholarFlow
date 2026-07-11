"""验证 arXiv Atom 条目的纯解析和统一论文映射。"""

from pathlib import Path  # 定位本地 Atom XML fixture 文件。

import pytest  # 提供映射异常断言工具。

from backend.app.adapters.arxiv import ArxivMappingError, map_arxiv_entry, parse_arxiv_atom_feed  # 导入待测 Atom 解析器、映射器和异常类型。


def _load_arxiv_entries() -> list[object]:
    """读取固定 arXiv Atom fixture 并返回论文条目。

    返回：
        list[object]：不依赖网络的 Atom 条目列表。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "arxiv_feed.xml"  # 根据测试文件位置构造 fixture 路径。
    return parse_arxiv_atom_feed(fixture_path.read_text(encoding="utf-8"))  # 使用 UTF-8 读取并解析本地 Atom XML。


def test_mapper_normalizes_arxiv_entry_with_provenance() -> None:
    """映射器应保留 arXiv 标识、预印本属性、分类、作者机构和公开 PDF。"""
    paper = map_arxiv_entry(_load_arxiv_entries()[0], raw_rank=2)  # 映射固定 Atom 条目并传入来源原始排名。
    assert paper.paper_id == "arxiv:2501.00001"  # 验证来源主键使用无版本 arXiv 标识。
    assert paper.arxiv_id == "2501.00001"  # 验证跨来源去重所需 arXiv 标识被保留。
    assert paper.year == 2025  # 验证首版投稿时间映射为展示年份。
    assert paper.authors[0].institution == "ScholarWeave Laboratory"  # 验证 arXiv 扩展作者机构被映射。
    assert paper.keywords == ["cs.LG", "cs.AI"]  # 验证来源分类被保留为关键词。
    assert paper.open_access_url == "https://arxiv.org/pdf/2501.00001v2"  # 验证来源提供的公开 PDF 链接被优先保留。
    assert paper.source_records[0].raw_rank == 2  # 验证 RRF 所需的来源排名被保留。


def test_mapper_rejects_entry_without_required_id() -> None:
    """缺少 Atom 论文标识时应返回可定位的映射错误。"""
    entry = _load_arxiv_entries()[0]  # 读取一条结构正确的 Atom 条目。
    entry.remove(entry.find("{http://www.w3.org/2005/Atom}id"))  # 删除论文标识构造最小无效条目。
    with pytest.raises(ArxivMappingError, match="id"):  # 断言错误指出缺少必要标识字段。
        map_arxiv_entry(entry)  # 映射无效条目并触发字段校验。
