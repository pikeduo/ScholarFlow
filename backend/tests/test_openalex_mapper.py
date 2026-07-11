"""验证 OpenAlex Work 响应的纯映射逻辑。"""

import json  # 加载本地 JSON fixture。
from pathlib import Path  # 定位测试 fixture 文件。

import pytest  # 提供异常断言工具。

from backend.app.adapters.openalex import OpenAlexMappingError, map_openalex_work_to_paper  # 导入待测映射器和异常类型。


def _load_openalex_work_fixture() -> dict[str, object]:
    """读取固定的 OpenAlex Work 响应样例。

    返回：
        dict[str, object]：不依赖网络的 OpenAlex Work fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "openalex_work.json"  # 根据测试文件位置构造 fixture 路径。
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # 以 UTF-8 解码并解析 JSON 数据。


def test_mapper_normalizes_openalex_work_to_paper() -> None:
    """映射器应还原标题、摘要、作者、期刊和引文关系。"""
    paper = map_openalex_work_to_paper(_load_openalex_work_fixture())  # 映射本地 OpenAlex Work fixture。
    assert paper.paper_id == "https://openalex.org/W1234567890"  # 验证 OpenAlex Work ID 映射。
    assert paper.abstract == "Large language models improve forecasting."  # 验证摘要倒排索引按位置还原。
    assert paper.authors[0].name == "Ada Lovelace"  # 验证嵌套作者名称映射。
    assert paper.authors[0].institution == "ScholarFlow Laboratory"  # 验证首个作者机构映射。
    assert paper.venue == "NeurIPS"  # 验证主发布位置的来源名称映射。
    assert paper.references == ["https://openalex.org/W0987654321"]  # 验证引用 Work ID 映射。


def test_mapper_rejects_work_without_required_id() -> None:
    """缺少 OpenAlex Work ID 时应返回可定位的映射错误。"""
    with pytest.raises(OpenAlexMappingError, match="id"):  # 断言错误指出缺少必要字段。
        map_openalex_work_to_paper({"title": "缺少标识的论文"})  # 构造没有 Work ID 的最小无效响应。
