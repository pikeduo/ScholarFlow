"""验证 PubMed E-utilities 客户端的离线请求、映射与错误边界。"""

import asyncio  # 在同步 pytest 用例中运行异步来源适配器。
import json  # 加载固定 ESearch JSON fixture。
from pathlib import Path  # 定位离线 XML 与 JSON fixture 文件。

import httpx  # 使用 MockTransport 拦截所有 HTTP 请求。
import pytest  # 提供来源异常断言工具。

from backend.app.adapters.base import AcademicSearchAdapter  # 验证客户端满足统一适配器协议。
from backend.app.adapters.pubmed import PubMedClient, PubMedClientError, build_pubmed_esearch_params  # 导入待测客户端、异常和参数构造器。
from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离配置。
from backend.app.models.query_intent import QueryIntent  # 构造统一来源契约需要的查询意图。


def _load_fixture(filename: str) -> str:
    """以 UTF-8 读取 PubMed 离线响应 fixture。"""
    fixture_path = Path(__file__).parent / "fixtures" / filename  # 根据测试文件位置定位固定来源响应。
    return fixture_path.read_text(encoding="utf-8")  # 显式使用 UTF-8 读取 JSON 或 XML 文本。


def _build_query_intent() -> QueryIntent:
    """构造可被 PubMed 统一适配器消费的最小有效查询意图。"""
    return QueryIntent(  # 构造不依赖 LLM、网络或用户本地配置的测试输入。
        original_query="Transformer clinical forecasting",  # 提供用户原始查询文本。
        normalized_query="Transformer clinical forecasting",  # 提供可复现的英文检索式。
        query_language="en",  # 标记检索式语言。
        research_topics=["clinical forecasting"],  # 提供主题检索词。
        methods=["Transformer"],  # 提供方法检索词。
        target_paper_count=5,  # 限制测试召回规模。
        source_recall_count=5,  # 显式验证独立来源召回规模优先级。
    )


def _build_test_settings() -> Settings:
    """构造不会读取真实 .env 且不产生节流等待的 PubMed 测试配置。"""
    return Settings(  # 注入测试专用值以稳定校验请求路径与通用参数。
        _env_file=None,  # 禁止从用户本地 .env 读取可能敏感的配置。
        pubmed_api_base_url="https://pubmed.test/eutils",  # 使用不可访问的测试域名避免误发真实请求。
        pubmed_requests_per_second=10,  # 取允许上限避免 ESearch 与 EFetch 之间出现长等待。
        pubmed_tool="ScholarFlowTest",  # 验证应用标识通过配置传递。
        pubmed_email="maintainer@example.test",  # 验证可选联系邮箱仅在 HTTP 边界添加。
        academic_api_max_retries=0,  # 错误边界测试不应等待默认指数退避。
    )


def test_esearch_params_map_query_intent_to_pubmed_fields() -> None:
    """参数构造器应将结构化检索词映射为 PubMed 的 ESearch 参数。"""
    params = build_pubmed_esearch_params(_build_query_intent())  # 构造不包含联系信息的纯检索参数。
    assert params == {  # 验证固定数据库、相关性排序、召回规模和确定性术语顺序。
        "db": "pubmed",
        "term": "(clinical forecasting) AND (Transformer)",
        "retmode": "json",
        "retmax": 5,
        "sort": "relevance",
    }


def test_esearch_params_preserve_query_intent_year_range() -> None:
    """参数构造器应将 QueryIntent 的年份元组映射为 PubMed 出版日期过滤条件。"""
    query = _build_query_intent().model_copy(update={"year_range": (2020, 2024)})  # 使用现有公开元组契约构造年份范围。
    params = build_pubmed_esearch_params(query)  # 生成包含日期限制的 PubMed 检索参数。
    assert params["term"] == "((clinical forecasting) AND (Transformer)) AND (2020:2024[dp])"  # 验证不依赖不存在的年份对象属性。


def test_client_implements_unified_adapter_and_maps_two_step_response() -> None:
    """客户端应完成 ESearch 到 EFetch 的两步调用并映射统一论文记录。"""
    esearch_payload = json.loads(_load_fixture("pubmed_esearch.json"))  # 加载确定性的 PMID 搜索响应。
    efetch_payload = _load_fixture("pubmed_efetch.xml")  # 加载包含论文元数据的确定性 XML 响应。
    request_paths: list[str] = []  # 记录请求顺序以验证两步 E-utilities 流程。

    def handler(request: httpx.Request) -> httpx.Response:
        """校验端点、参数和调用顺序后返回离线 PubMed 响应。"""
        request_paths.append(request.url.path)  # 记录当前访问的 E-utilities 子路径。
        assert request.url.params["db"] == "pubmed"  # 两个端点均必须显式指定 PubMed 数据库。
        assert request.url.params["tool"] == "ScholarFlowTest"  # 验证应用标识由配置安全注入。
        assert request.url.params["email"] == "maintainer@example.test"  # 验证已配置联系邮箱随请求发送。
        if request.url.path == "/eutils/esearch.fcgi":  # ESearch 只负责返回按相关性排序的 PMID。
            assert request.url.params["term"] == "(clinical forecasting) AND (Transformer)"  # 验证结构化意图被稳定拼接。
            assert request.url.params["retmax"] == "5"  # 验证来源召回规模被传入 ESearch。
            return httpx.Response(200, json=esearch_payload, request=request)  # 返回无网络依赖的 PMID 列表。
        if request.url.path == "/eutils/efetch.fcgi":  # EFetch 负责批量返回论文 XML。
            assert request.url.params["id"] == "12345678"  # 验证 ESearch PMID 被原样传递到 EFetch。
            assert request.url.params["retmode"] == "xml"  # 验证显式请求 XML 元数据格式。
            return httpx.Response(200, text=efetch_payload, request=request)  # 返回无网络依赖的文章 XML。
        raise AssertionError(f"意外的 PubMed 请求路径：{request.url.path}")  # 防止客户端额外访问未约定端点。

    client = PubMedClient(settings_override=_build_test_settings(), transport=httpx.MockTransport(handler))  # 使用 MockTransport 装配完全离线的来源客户端。
    assert isinstance(client, AcademicSearchAdapter)  # 验证可由多源协调器按统一协议消费。
    papers = asyncio.run(client.search(_build_query_intent()))  # 执行离线的 ESearch 与 EFetch 映射流程。
    assert request_paths == ["/eutils/esearch.fcgi", "/eutils/efetch.fcgi"]  # 验证严格执行先搜索、后取详情的两步流程。
    assert len(papers) == 1  # 验证成功映射单个 fixture 条目。
    assert papers[0].paper_id == "pubmed:12345678"  # 验证来源命名空间与 PMID 保持稳定。
    assert papers[0].pmid == "12345678"  # 验证 PMID 可供后续身份去重。
    assert papers[0].doi == "10.1000/pubmed-test"  # 验证 DOI 被提取为跨来源首要身份标识。
    assert papers[0].authors[0].name == "Ada Lovelace"  # 验证个人作者姓名按可展示顺序映射。
    assert papers[0].authors[1].name == "ScholarFlow Consortium"  # 验证集体作者不被丢弃。
    assert papers[0].source_records[0].raw_rank == 1  # 验证 ESearch 原始排名保留给 RRF 融合。


def test_client_returns_empty_without_efetch_when_esearch_has_no_pmids() -> None:
    """PubMed 正常空结果应直接返回空列表而不触发第二次请求。"""
    request_count = 0  # 记录请求数以验证空结果不会额外消耗来源配额。

    def handler(request: httpx.Request) -> httpx.Response:
        """返回合法但无 PMID 的 ESearch 响应。"""
        nonlocal request_count  # 修改外层请求计数器。
        request_count += 1  # 记录本次 E-utilities 调用。
        return httpx.Response(200, json={"esearchresult": {"idlist": []}}, request=request)  # 构造来源正常空结果。

    client = PubMedClient(settings_override=_build_test_settings(), transport=httpx.MockTransport(handler))  # 使用离线传输层构造客户端。
    assert asyncio.run(client.search(_build_query_intent())) == []  # 验证无 PMID 时返回稳定空集合。
    assert request_count == 1  # 验证未调用 EFetch。


def test_client_hides_http_error_details() -> None:
    """上游 HTTP 错误应转换为不包含响应正文的领域异常。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """返回不应向调用方暴露的来源错误正文。"""
        return httpx.Response(429, text="upstream sensitive details", request=request)  # 模拟来源限流响应。

    client = PubMedClient(settings_override=_build_test_settings(), transport=httpx.MockTransport(handler))  # 使用离线失败传输层构造客户端。
    with pytest.raises(PubMedClientError, match="HTTP 429") as error_info:  # 仅允许调用方获得净化后的状态类别。
        asyncio.run(client.search(_build_query_intent()))  # 触发第一步 ESearch 的 HTTP 错误。
    assert "upstream sensitive details" not in str(error_info.value)  # 验证上游响应正文未泄露。
