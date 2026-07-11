"""验证 OpenAlex 异步客户端的本地 mock 调用行为。"""

import asyncio  # 在同步 pytest 用例中运行异步客户端方法。
import json  # 加载本地 OpenAlex Work fixture。
from pathlib import Path  # 定位测试 fixture 文件。

import httpx  # 使用 MockTransport 拦截 HTTP 请求。
import pytest  # 提供异常断言工具。

from backend.app.adapters.openalex import OpenAlexClient, OpenAlexClientError  # 导入待测客户端和领域异常。
from backend.app.core.config import Settings  # 构造隔离的测试配置。
from backend.app.models.query import QuerySchema  # 构造客户端查询输入。


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
