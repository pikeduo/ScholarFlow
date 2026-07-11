"""验证 Semantic Scholar 客户端的离线请求、协议和错误边界。"""

import asyncio  # 在同步 pytest 用例中运行异步来源适配器。
import json  # 加载本地 Semantic Scholar JSON fixture。
from pathlib import Path  # 定位测试 fixture 文件。
from unittest.mock import AsyncMock, patch  # 跳过限流重试的真实等待并验证异步调用。

import httpx  # 使用 MockTransport 拦截 HTTP 请求。
import pytest  # 提供异常断言工具。

from backend.app.adapters.base import AcademicSearchAdapter  # 验证客户端满足统一来源协议。
from backend.app.adapters.semantic_scholar import SemanticScholarClient, SemanticScholarClientError  # 导入待测客户端和领域异常。
from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离配置。
from backend.app.models.query_intent import QueryIntent  # 构造统一适配器输入。


def _load_semantic_scholar_paper_fixture() -> dict[str, object]:
    """读取固定的 Semantic Scholar 论文响应样例。

    返回：
        dict[str, object]：不依赖网络的单条论文 fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "semantic_scholar_paper.json"  # 根据测试文件位置构造 fixture 路径。
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # 使用 UTF-8 解码并解析 JSON 数据。


def _build_query_intent() -> QueryIntent:
    """构造可被统一来源协议消费的最小有效查询意图。"""
    return QueryIntent(  # 构造无需 LLM 或网络的查询规划结果。
        original_query="Transformer forecasting on ETT",  # 提供用户原始查询。
        normalized_query="Transformer forecasting ETT",  # 提供可复现的规范化查询。
        query_language="en",  # 标记查询语言。
        research_topics=["forecasting"],  # 提供主题检索词。
        methods=["Transformer"],  # 提供方法检索词。
        datasets=["ETT"],  # 提供数据集检索词。
        target_paper_count=5,  # 限制测试请求规模。
    )


def _build_test_settings(api_key: str | None = "test-api-key") -> Settings:
    """构造不读取真实 .env 的 Semantic Scholar 测试配置。"""
    return Settings(_env_file=None, semantic_scholar_api_key=api_key, semantic_scholar_max_retries=0)  # 注入测试密钥并默认关闭等待重试。


def test_client_implements_unified_adapter_and_maps_search_response() -> None:
    """客户端应满足统一协议、发送认证头并映射来源排名。"""
    fixture = _load_semantic_scholar_paper_fixture()  # 读取本地论文 fixture。

    def handler(request: httpx.Request) -> httpx.Response:
        """校验请求参数和请求头并返回本地成功响应。"""
        assert request.url.path == "/graph/v1/paper/search"  # 验证基地址版本前缀与论文搜索端点被正确拼接。
        assert request.url.params["query"] == "forecasting Transformer ETT"  # 验证查询意图按确定顺序转换为全文搜索词。
        assert request.url.params["limit"] == "5"  # 验证目标结果数量映射为来源单页限制。
        assert request.headers["x-api-key"] == "test-api-key"  # 验证密钥仅在认证请求头中注入。
        return httpx.Response(200, json={"data": [fixture]}, request=request)  # 返回不依赖网络的官方结构响应。

    client = SemanticScholarClient(  # 使用 mock 传输层构造来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    assert isinstance(client, AcademicSearchAdapter)  # 验证客户端满足统一来源适配器协议。
    papers = asyncio.run(client.search(_build_query_intent()))  # 执行不访问网络的异步单页搜索。
    assert papers[0].source_records[0].raw_rank == 1  # 验证客户端为首条结果写入来源排名。


def test_client_allows_anonymous_access_when_key_is_not_configured() -> None:
    """未配置可选 API Key 时客户端应不发送空认证头并继续使用匿名端点。"""
    fixture = _load_semantic_scholar_paper_fixture()  # 读取本地论文 fixture。

    def handler(request: httpx.Request) -> httpx.Response:
        """验证匿名请求不包含认证头并返回本地成功响应。"""
        assert "x-api-key" not in request.headers  # 验证不会将空密钥发送给来源服务。
        return httpx.Response(200, json={"data": [fixture]}, request=request)  # 返回本地匿名成功响应。

    client = SemanticScholarClient(  # 使用匿名隔离配置构造来源客户端。
        settings_override=_build_test_settings(api_key=None),  # 注入未配置密钥的测试设置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    papers = asyncio.run(client.search(_build_query_intent()))  # 执行匿名单页搜索。
    assert [paper.paper_id for paper in papers] == ["S2-paper-123"]  # 验证匿名访问仍可映射返回论文。


def test_client_hides_http_error_details() -> None:
    """非成功 HTTP 状态应转换为不泄露请求头或响应正文的领域错误。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """返回模拟来源限流响应。"""
        return httpx.Response(429, request=request)  # 模拟 Semantic Scholar 限流。

    client = SemanticScholarClient(  # 使用 mock 限流响应构造来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    with pytest.raises(SemanticScholarClientError, match="HTTP 429"):  # 断言调用方仅收到已净化状态错误。
        asyncio.run(client.search(_build_query_intent()))  # 执行并触发来源错误边界。


@pytest.mark.parametrize(  # 覆盖供应商可能以 HTTP 200 返回的常见错误信封。
    ("payload", "expected_category"),
    [
        ({"message": "Too many requests; status code 429"}, "请求受限"),  # 模拟限流错误信封。
        ({"error": "Invalid API key"}, "认证失败"),  # 模拟无效认证错误信封。
        ({"detail": "Invalid fields parameter"}, "请求参数被拒绝"),  # 模拟字段参数拒绝错误信封。
        ({"message": "Internal server error 503"}, "供应商暂时不可用"),  # 模拟供应商暂时故障。
        ({"unexpected": True}, "响应结构无效"),  # 模拟未知结构响应。
    ],
)
def test_client_classifies_success_status_error_envelope(payload: dict[str, object], expected_category: str) -> None:
    """HTTP 200 但缺少 data 时客户端应给出安全错误分类而不返回原始正文。"""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload, request=request))  # 返回完全离线的供应商错误信封。
    client = SemanticScholarClient(settings_override=_build_test_settings(), transport=transport)  # 使用隔离配置构造来源客户端。

    with pytest.raises(SemanticScholarClientError, match=expected_category):  # 验证调用方获得稳定分类。
        asyncio.run(client.search(_build_query_intent()))  # 执行并触发非标准响应分类。


def test_client_retries_success_status_rate_limit_envelope() -> None:
    """供应商以 HTTP 200 返回限流信封时客户端应等待并在预算内重试。"""
    fixture = _load_semantic_scholar_paper_fixture()  # 读取重试成功时返回的本地论文样例。
    request_count = 0  # 统计 MockTransport 收到的调用次数。

    def handler(request: httpx.Request) -> httpx.Response:
        """首次返回限流信封，第二次返回正常 data。"""
        nonlocal request_count  # 更新当前用例的调用计数。
        request_count += 1  # 记录本次来源请求。
        payload = {"message": "Too many requests 429"} if request_count == 1 else {"data": [fixture]}  # 按次数切换响应。
        return httpx.Response(200, json=payload, request=request)  # 保持完全离线的成功状态响应。

    settings = Settings(_env_file=None, semantic_scholar_api_key="test-api-key", semantic_scholar_max_retries=1)  # 允许一次限流重试。
    client = SemanticScholarClient(settings_override=settings, transport=httpx.MockTransport(handler))  # 注入离线来源响应。
    with patch("backend.app.adapters.semantic_scholar.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:  # 跳过真实一秒等待。
        papers = asyncio.run(client.search(_build_query_intent()))  # 执行限流后成功路径。

    assert request_count == 2  # 验证只发起首次调用和一次重试。
    assert [paper.paper_id for paper in papers] == ["S2-paper-123"]  # 验证重试成功结果正常映射。
    sleep_mock.assert_awaited_once_with(1.0)  # 验证遵守至少一秒的来源重试间隔。
