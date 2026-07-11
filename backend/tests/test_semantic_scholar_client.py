"""验证 Semantic Scholar 客户端的离线请求、协议和错误边界。"""

import asyncio  # 在同步 pytest 用例中运行异步来源适配器。
import json  # 加载本地 Semantic Scholar JSON fixture。
from pathlib import Path  # 定位测试 fixture 文件。

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
    return Settings(_env_file=None, semantic_scholar_api_key=api_key)  # 注入无实际权限的测试密钥或匿名访问配置。


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
