"""验证 Tavily 补充网页发现客户端的离线请求、协议和错误边界。"""

import asyncio  # 在同步 pytest 用例中运行异步补充发现客户端。
import json  # 加载本地 Tavily 搜索 fixture 并检查请求 JSON。
from pathlib import Path  # 定位测试 fixture 文件。

import httpx  # 使用 MockTransport 拦截 HTTP 请求。
import pytest  # 提供异常断言工具。

from backend.app.adapters.base import WebDiscoveryAdapter  # 验证客户端满足补充发现协议。
from backend.app.adapters.tavily import TavilyClient, TavilyClientError, build_tavily_search_payload  # 导入待测客户端、异常和请求构造器。
from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离配置。
from backend.app.models.query_intent import QueryIntent  # 构造补充发现协议要求的查询输入。


def _load_tavily_payload_fixture() -> dict[str, object]:
    """读取固定的 Tavily 搜索响应样例。

    返回：
        dict[str, object]：不依赖网络的完整 Tavily 响应 fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "tavily_search.json"  # 根据测试文件位置构造 fixture 路径。
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # 使用 UTF-8 解码并解析 JSON 数据。


def _build_query_intent() -> QueryIntent:
    """构造可被 Tavily 补充发现协议消费的最小有效查询意图。

    返回：
        QueryIntent：包含主题、方法和目标数量的离线测试意图。
    """
    return QueryIntent(  # 构造无需 LLM 或网络的查询规划结果。
        original_query="Transformer forecasting",  # 提供用户原始查询文本。
        normalized_query="Transformer forecasting",  # 提供可复现的规范化查询文本。
        query_language="en",  # 标记查询语言。
        research_topics=["forecasting"],  # 提供主题检索词。
        methods=["Transformer"],  # 提供方法检索词。
        target_paper_count=5,  # 构造大于补充来源配置上限的目标数量。
    )


def _build_test_settings(api_key: str | None = "test-api-key") -> Settings:
    """构造不读取真实 .env 的 Tavily 测试配置。

    返回：
        Settings：携带虚拟密钥、无等待频率与补充结果上限的隔离设置。
    """
    return Settings(  # 构造仅供离线 MockTransport 使用的隔离配置。
        _env_file=None,  # 禁止测试读取用户本地配置值。
        tavily_api_key=api_key,  # 注入不具备真实权限的测试密钥或缺失配置。
        tavily_requests_per_second=5,  # 提升测试频率以避免重复调用时产生实际等待。
        tavily_max_results=3,  # 验证补充来源结果数会被独立预算裁剪。
    )


def test_payload_excludes_raw_content_and_limits_results() -> None:
    """Tavily 请求体应禁用完整正文并使用经过预算裁剪的结果数量。"""
    payload = build_tavily_search_payload(_build_query_intent(), max_results=3)  # 构造不含密钥的来源请求体。
    assert payload["query"] == "forecasting Transformer"  # 验证查询意图按确定顺序转换为网页发现词。
    assert payload["max_results"] == 3  # 验证请求使用补充来源独立结果上限。
    assert payload["include_raw_content"] is False  # 验证不会请求完整网页正文。
    assert payload["include_answer"] is False  # 验证不会请求不可直接溯源的生成式答案。


def test_client_implements_discovery_adapter_and_returns_non_mergeable_items() -> None:
    """客户端应满足补充发现协议、发送 Bearer 认证并返回不可合并网页结果。"""
    fixture = _load_tavily_payload_fixture()  # 读取本地 JSON 响应。

    def handler(request: httpx.Request) -> httpx.Response:
        """校验请求方法、认证头和 JSON 请求体并返回本地成功响应。"""
        assert request.method == "POST"  # 验证客户端调用官方 POST Search 端点。
        assert request.url.path == "/search"  # 验证客户端调用正确搜索路径。
        assert request.headers["authorization"] == "Bearer test-api-key"  # 验证密钥仅在认证请求头中注入。
        assert request.headers["content-type"].startswith("application/json")  # 验证请求体按 JSON 发送。
        assert json.loads(request.content.decode("utf-8"))["max_results"] == 3  # 验证请求结果数遵守补充来源上限。
        return httpx.Response(200, json=fixture, request=request)  # 返回不依赖网络的 Tavily 响应。

    client = TavilyClient(  # 使用 mock 传输层构造补充发现客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    assert isinstance(client, WebDiscoveryAdapter)  # 验证客户端满足独立补充发现协议。
    discoveries = asyncio.run(client.discover(_build_query_intent()))  # 执行不访问网络的异步补充发现。
    assert discoveries[0].mergeable_as_paper is False  # 验证网页结果不会进入论文融合流程。
    assert discoveries[0].raw_rank == 1  # 验证首条结果保留来源原始排名。


def test_client_rejects_missing_api_key_before_request() -> None:
    """缺少 Tavily API Key 时客户端应在网络请求前返回稳定配置错误。"""
    client = TavilyClient(settings_override=_build_test_settings(api_key=None))  # 构造未配置密钥的隔离客户端。
    with pytest.raises(TavilyClientError, match="Tavily 服务尚未配置"):  # 断言调用方不会收到密钥字段或其内容。
        asyncio.run(client.discover(_build_query_intent()))  # 在 HTTP 请求前触发密钥配置校验。
