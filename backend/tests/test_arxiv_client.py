"""验证 arXiv 客户端的离线 Atom 请求、协议和错误边界。"""

import asyncio  # 在同步 pytest 用例中运行异步来源适配器。
from pathlib import Path  # 定位本地 Atom XML fixture 文件。

import httpx  # 使用 MockTransport 拦截 HTTP 请求。
import pytest  # 提供异常断言工具。

from backend.app.adapters.arxiv import ArxivClient, ArxivClientError, build_arxiv_search_params  # 导入待测客户端、异常和参数构造器。
from backend.app.adapters.base import AcademicSearchAdapter  # 验证客户端满足统一来源协议。
from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离配置。
from backend.app.models.query_intent import QueryIntent  # 构造统一来源协议要求的查询输入。


def _load_arxiv_feed_fixture() -> str:
    """读取固定的 arXiv Atom XML 响应样例。

    返回：
        str：不依赖网络的 Atom XML fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "arxiv_feed.xml"  # 根据测试文件位置构造 fixture 路径。
    return fixture_path.read_text(encoding="utf-8")  # 使用 UTF-8 读取完整 Atom XML 文本。


def _build_query_intent() -> QueryIntent:
    """构造可被 arXiv 统一来源协议消费的最小有效查询意图。

    返回：
        QueryIntent：包含主题、方法、年份与目标数量的离线测试意图。
    """
    return QueryIntent(  # 构造无需 LLM 或网络的查询规划结果。
        original_query="Transformer forecasting after 2020",  # 提供用户原始查询文本。
        normalized_query="Transformer forecasting",  # 提供可复现的规范化查询文本。
        query_language="en",  # 标记查询语言。
        research_topics=["forecasting"],  # 提供主题检索词。
        methods=["Transformer"],  # 提供方法检索词。
        year_range=(2020, 2024),  # 提供以提交时间近似执行的年份范围。
        target_paper_count=5,  # 限制测试请求规模。
    )


def _build_test_settings() -> Settings:
    """构造不读取真实 .env 的 arXiv 测试配置。

    返回：
        Settings：携带测试用地址、超时和无等待频率的隔离设置。
    """
    return Settings(_env_file=None, arxiv_requests_per_second=1, academic_api_max_retries=0)  # 提升测试频率并避免错误边界测试真实等待。


def test_search_params_quote_terms_and_map_submitted_date_range() -> None:
    """参数构造器应保护用户词语并将年份明确映射为提交日期近似过滤。"""
    params = build_arxiv_search_params(_build_query_intent())  # 构造不含网络或密钥的来源参数。
    assert params["search_query"] == 'all:"forecasting" AND (all:"Transformer" OR all:"Transformers") AND submittedDate:[202001010000 TO 202412312359]'  # 验证核心概念 AND、词形 OR 与提交日期范围。
    assert params["max_results"] == 5  # 验证目标结果数量映射为来源单页限制。


def test_client_implements_unified_adapter_and_maps_atom_response() -> None:
    """客户端应满足协议、调用 Query API 并映射带来源溯源的论文记录。"""
    fixture = _load_arxiv_feed_fixture()  # 读取本地 Atom XML 响应。

    def handler(request: httpx.Request) -> httpx.Response:
        """校验请求参数并返回本地成功 Atom 响应。"""
        assert request.url.path == "/api/query"  # 验证基地址路径与 Query 端点被正确拼接。
        assert request.url.params["max_results"] == "5"  # 验证目标数量写入官方 max_results 参数。
        assert request.headers["user-agent"] == "ScholarWeave/0.1 (academic-search)"  # 验证来源请求携带可识别但不含用户数据的客户端标识。
        return httpx.Response(200, text=fixture, headers={"content-type": "application/atom+xml; charset=utf-8"}, request=request)  # 返回不依赖网络的 Atom 响应。

    client = ArxivClient(  # 使用 mock 传输层构造统一来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    assert isinstance(client, AcademicSearchAdapter)  # 验证客户端满足统一来源适配器协议。
    papers = asyncio.run(client.search(_build_query_intent()))  # 执行不访问网络的异步单页搜索。
    assert papers[0].source == "arxiv"  # 验证统一记录标记正确来源。
    assert papers[0].source_records[0].external_id == "2501.00001"  # 验证来源稳定标识保留无版本 arXiv ID。


def test_client_hides_http_error_details() -> None:
    """非成功 HTTP 状态应转换为不泄露响应正文的领域错误。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """返回模拟来源过载响应。"""
        return httpx.Response(503, text="upstream details", request=request)  # 模拟不应暴露正文的来源服务异常。

    client = ArxivClient(  # 使用 mock 失败响应构造来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    with pytest.raises(ArxivClientError, match="HTTP 503"):  # 断言调用方仅收到已净化状态错误。
        asyncio.run(client.search(_build_query_intent()))  # 执行并触发来源错误边界。
