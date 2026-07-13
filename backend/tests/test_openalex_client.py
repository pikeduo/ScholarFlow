"""验证 OpenAlex 异步客户端的本地 mock 调用行为。"""

import asyncio  # 在同步 pytest 用例中运行异步客户端方法。
import json  # 加载本地 OpenAlex Work fixture。
from pathlib import Path  # 定位测试 fixture 文件。

import httpx  # 使用 MockTransport 拦截 HTTP 请求。
import pytest  # 提供异常断言工具。

from backend.app.adapters.openalex import OpenAlexClient, OpenAlexClientError  # 导入待测客户端和领域异常。
from backend.app.core.config import Settings  # 构造隔离的测试配置。
from backend.app.models.query import QuerySchema  # 构造客户端查询输入。


class FakeResponseCache:
    """提供可记录命中状态的来源响应缓存替身。"""

    def __init__(self) -> None:
        """初始化按测试键保存的来源响应数组。"""
        self.values: dict[str, list[object]] = {}  # 保存不需要真实 Redis 的缓存值。

    def build_key(self, source: str, operation: str, params: object, adapter_version: str = "v1") -> str:
        """构造稳定测试键，不参与缓存键算法本身的单元验证。"""
        return f"{source}:{operation}:{adapter_version}:{params}"  # 让同一请求两次获得相同测试键。

    async def get_list(self, key: str, source: str, operation: str) -> list[object] | None:
        """返回已缓存的来源原始数组或空值。"""
        return self.values.get(key)  # 模拟 Redis 命中和未命中语义。

    async def set_list(self, key: str, source: str, operation: str, value: list[object]) -> None:
        """保存来源原始数组供下一次适配器调用读取。"""
        self.values[key] = value  # 模拟带 TTL 的成功旁路写入。


def _load_openalex_work_fixture() -> dict[str, object]:
    """读取固定的 OpenAlex Work 响应样例。

    返回：
        dict[str, object]：不依赖网络的 OpenAlex Work fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "openalex_work.json"  # 根据测试文件位置构造 fixture 路径。
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # 以 UTF-8 解码并解析 JSON 数据。


def _build_test_settings() -> Settings:
    """构造不读取真实 .env 的 OpenAlex 测试配置。"""
    return Settings(_env_file=None, openalex_api_key="test-api-key")  # 注入没有实际权限的测试密钥。


def test_client_requests_works_and_maps_results() -> None:
    """客户端应注入密钥、调用 /works 并映射返回结果。"""
    fixture = _load_openalex_work_fixture()  # 读取本地 Work 响应。

    def handler(request: httpx.Request) -> httpx.Response:
        """校验请求参数并返回本地成功响应。"""
        assert request.url.path == "/works"  # 验证客户端调用正确端点。
        assert request.url.params["api_key"] == "test-api-key"  # 验证密钥只在请求层注入。
        assert request.url.params["search"] == "forecasting"  # 验证查询参数构造器已被复用。
        return httpx.Response(200, json={"results": [fixture]}, request=request)  # 返回不依赖网络的 API 响应。

    client = OpenAlexClient(  # 使用 mock 传输层构造客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    papers = asyncio.run(client.search_works(QuerySchema(topic=["forecasting"])))  # 执行异步搜索方法。
    assert [paper.paper_id for paper in papers] == ["https://openalex.org/W1234567890"]  # 验证成功映射并返回论文。


def test_client_hides_http_error_details() -> None:
    """非成功 HTTP 状态应转换为不泄露请求参数的领域错误。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """返回模拟限流响应。"""
        return httpx.Response(429, request=request)  # 模拟 OpenAlex 限流。

    client = OpenAlexClient(  # 使用 mock 限流响应构造客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    with pytest.raises(OpenAlexClientError, match="HTTP 429"):  # 断言返回已净化的状态错误。
        asyncio.run(client.search_works(QuerySchema(topic=["forecasting"])))  # 执行异步搜索方法。


def test_client_hides_missing_api_key_configuration() -> None:
    """缺少 API 密钥时客户端应返回不暴露环境变量值的领域错误。"""
    client = OpenAlexClient(settings_override=Settings(_env_file=None))  # 构造未配置 API 密钥的隔离客户端。
    with pytest.raises(OpenAlexClientError, match="OpenAlex 服务尚未配置"):  # 断言调用方不会收到密钥字段或其内容。
        asyncio.run(client.search_works(QuerySchema(topic=["forecasting"])))  # 在 HTTP 请求前触发配置校验。


def test_client_uses_cached_works_response_before_second_network_request() -> None:
    """同一 OpenAlex 请求第二次应使用已缓存响应，不再进入 HTTP 传输层。"""
    fixture = _load_openalex_work_fixture()  # 读取本地 Work 响应样例。
    request_count = 0  # 统计实际进入来源 HTTP 传输层的次数。

    def handler(request: httpx.Request) -> httpx.Response:
        """记录来源调用并返回固定成功响应。"""
        nonlocal request_count  # 更新当前测试闭包中的调用计数。
        request_count += 1  # 记录一次真实 HTTP 适配器调用。
        return httpx.Response(200, json={"results": [fixture]}, request=request)  # 返回离线固定成功响应。

    client = OpenAlexClient(  # 构造带内存缓存替身的来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离测试密钥。
        transport=httpx.MockTransport(handler),  # 阻止真实网络访问。
        response_cache=FakeResponseCache(),  # 注入可观察缓存命中的替身。
    )
    query = QuerySchema(topic=["forecasting"])  # 构造两次完全相同的来源查询。
    asyncio.run(client.search_works(query))  # 首次调用应回源并写入缓存。
    asyncio.run(client.search_works(query))  # 第二次调用应直接读取缓存。
    assert request_count == 1  # 验证缓存命中没有额外消耗 OpenAlex 调用次数。
