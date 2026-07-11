"""验证 Tavily 网页结果映射为不可合并补充发现项的边界。"""

import json  # 加载本地 Tavily 搜索 fixture。
from pathlib import Path  # 定位测试 fixture 文件。

import pytest  # 提供映射异常断言工具。

from backend.app.adapters.tavily import TavilyMappingError, map_tavily_result  # 导入待测映射器和异常类型。


def _load_tavily_result_fixture() -> dict[str, object]:
    """读取固定的 Tavily 单条网页结果样例。

    返回：
        dict[str, object]：不依赖网络的单条 Tavily result fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "tavily_search.json"  # 根据测试文件位置构造 fixture 路径。
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))  # 使用 UTF-8 解码并解析 JSON 数据。
    return payload["results"][0]  # 返回已知 fixture 路径下的单条网页结果对象。


def test_mapper_marks_tavily_result_as_non_mergeable_discovery() -> None:
    """映射器必须将 Tavily 网页结果标记为不可直接合并的补充发现项。"""
    item = map_tavily_result(_load_tavily_result_fixture(), raw_rank=2)  # 映射固定 fixture 并传入来源原始排名。
    assert item.source == "tavily"  # 验证补充发现来源被显式标记。
    assert item.mergeable_as_paper is False  # 验证网页结果不会被当作论文进入去重与引文流程。
    assert item.url == "https://example.org/transformer-forecasting"  # 验证来源网页地址被保留为证据入口。
    assert item.relevance_score == 0.92  # 验证来源相关性分数被保留。
    assert item.raw_rank == 2  # 验证来源原始排名被保留。


def test_mapper_rejects_non_http_url() -> None:
    """非 HTTP 网页地址不能被映射为前端可访问的补充发现项。"""
    with pytest.raises(TavilyMappingError, match="HTTP URL"):  # 断言错误指出网页协议不合法。
        map_tavily_result({"title": "无效链接", "url": "file:///private"}, raw_rank=1)  # 构造不应出现在网页证据中的本地协议链接。
