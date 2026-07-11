"""验证 DBLP 客户端的离线请求、协议和错误边界。"""

import asyncio  # 在同步 pytest 用例中运行异步来源适配器。
import json  # 加载本地 DBLP 出版物 fixture。
from pathlib import Path  # 定位测试 fixture 文件。

import httpx  # 使用 MockTransport 拦截 HTTP 请求。
import pytest  # 提供异常断言工具。

from backend.app.adapters.base import AcademicSearchAdapter  # 验证客户端满足统一来源协议。
from backend.app.adapters.dblp import DblpClient, DblpClientError, build_dblp_search_params  # 导入待测客户端、异常和参数构造器。
from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离配置。
from backend.app.models.query_intent import QueryIntent  # 构造统一来源协议要求的查询输入。


def _load_dblp_payload_fixture() -> dict[str, object]:
    """读取固定的 DBLP 出版物搜索响应样例。

    返回：
        dict[str, object]：不依赖网络的完整 DBLP 响应 fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "dblp_publication.json"  # 根据测试文件位置构造 fixture 路径。
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # 使用 UTF-8 解码并解析 JSON 数据。


def _build_query_intent() -> QueryIntent:
    """构造可被 DBLP 统一来源协议消费的最小有效查询意图。

    返回：
        QueryIntent：包含主题、方法和目标数量的离线测试意图。
    """
    return QueryIntent(  # 构造无需 LLM 或网络的查询规划结果。
        original_query="Transformer forecasting",  # 提供用户原始查询文本。
        normalized_query="Transformer forecasting",  # 提供可复现的规范化查询文本。
        query_language="en",  # 标记查询语言。
        research_topics=["forecasting"],  # 提供主题检索词。
        methods=["Transformer"],  # 提供方法检索词。
        target_paper_count=5,  # 限制测试请求规模。
    )


def _build_test_settings() -> Settings:
    """构造不读取真实 .env 的 DBLP 测试配置。

    返回：
        Settings：携带无等待频率的隔离设置。
    """
    return Settings(_env_file=None, dblp_requests_per_second=5)  # 提升测试频率以避免重复调用时产生实际等待。


def test_search_params_map_query_intent_to_official_fields() -> None:
    """参数构造器应将统一意图映射为 DBLP 的 q、format、h 与 f 参数。"""
    params = build_dblp_search_params(_build_query_intent())  # 构造不含网络或密钥的来源参数。
    assert params == {"q": "forecasting Transformer", "format": "json", "h": 5, "f": 0}  # 验证官方请求字段及确定性检索词顺序。


def test_client_implements_unified_adapter_and_maps_search_response() -> None:
    """客户端应满足协议、调用出版物端点并映射带来源溯源的论文记录。"""
    fixture = _load_dblp_payload_fixture()  # 读取本地 JSON 响应。

    def handler(request: httpx.Request) -> httpx.Response:
        """校验请求参数并返回本地成功响应。"""
        assert request.url.path == "/search/publ/api"  # 验证基地址路径与出版物搜索端点被正确拼接。
        assert request.url.params["q"] == "forecasting Transformer"  # 验证查询意图按确定顺序映射为 DBLP 搜索词。
        assert request.url.params["h"] == "5"  # 验证目标结果数量映射为来源最大命中数。
        assert request.url.params["format"] == "json"  # 验证客户端显式请求官方 JSON 响应格式。
        return httpx.Response(200, json=fixture, request=request)  # 返回不依赖网络的 DBLP 响应。

    client = DblpClient(  # 使用 mock 传输层构造统一来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    assert isinstance(client, AcademicSearchAdapter)  # 验证客户端满足统一来源适配器协议。
    papers = asyncio.run(client.search(_build_query_intent()))  # 执行不访问网络的异步单页搜索。
    assert papers[0].dblp_key == "conf/aaai/Lovelace25"  # 验证来源稳定 DBLP 键被保留。
    assert papers[0].source_records[0].raw_rank == 1  # 验证首条结果写入 RRF 所需的来源排名。


def test_client_hides_http_error_details() -> None:
    """非成功 HTTP 状态应转换为不泄露响应正文的领域错误。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """返回模拟来源限流响应。"""
        return httpx.Response(429, text="upstream details", request=request)  # 模拟不应暴露正文的来源限流异常。

    client = DblpClient(  # 使用 mock 失败响应构造来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    with pytest.raises(DblpClientError, match="HTTP 429"):  # 断言调用方仅收到已净化状态错误。
        asyncio.run(client.search(_build_query_intent()))  # 执行并触发来源错误边界。
