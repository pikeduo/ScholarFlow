"""验证 DBLP 出版物 JSON 的纯映射与来源溯源逻辑。"""

import json  # 加载本地 DBLP 出版物 fixture。
from pathlib import Path  # 定位测试 fixture 文件。

import pytest  # 提供映射异常断言工具。

from backend.app.adapters.dblp import DblpMappingError, map_dblp_hit  # 导入待测映射器和异常类型。


def _load_dblp_hit_fixture() -> dict[str, object]:
    """读取固定的 DBLP 出版物命中样例。

    返回：
        dict[str, object]：不依赖网络的单条 DBLP hit fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "dblp_publication.json"  # 根据测试文件位置构造 fixture 路径。
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))  # 使用 UTF-8 解码并解析 JSON 数据。
    return payload["result"]["hits"]["hit"]  # 返回已知 fixture 路径下的单条 DBLP 命中对象。


def test_mapper_normalizes_dblp_hit_with_provenance() -> None:
    """映射器应保留 DBLP 键、作者、类型、DOI 与来源原始排名。"""
    paper = map_dblp_hit(_load_dblp_hit_fixture(), raw_rank=3)  # 映射固定 fixture 并传入来源原始排名。
    assert paper.paper_id == "dblp:conf/aaai/Lovelace25"  # 验证来源主键使用带前缀的 DBLP 键。
    assert paper.dblp_key == "conf/aaai/Lovelace25"  # 验证 DBLP 键被显式保留。
    assert paper.title == "Transformer & Forecasting: A DBLP Example"  # 验证 HTML 实体与展示标签被清理。
    assert [author.name for author in paper.authors] == ["Ada Lovelace", "Grace Hopper"]  # 验证多作者顺序被保留。
    assert paper.paper_type == "conference"  # 验证会议类型被映射为统一类型。
    assert paper.source_records[0].raw_rank == 3  # 验证 RRF 所需的来源排名被保留。


def test_mapper_rejects_hit_without_required_key() -> None:
    """缺少 DBLP 出版物键时应返回可定位的映射错误。"""
    with pytest.raises(DblpMappingError, match="key"):  # 断言错误指出缺少必要来源标识。
        map_dblp_hit({"info": {"title": "缺少标识的论文"}})  # 构造没有 DBLP key 的最小无效命中。
