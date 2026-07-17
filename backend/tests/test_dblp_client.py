"""验证 DBLP 客户端的离线请求、协议和错误边界。"""

import asyncio  # 在同步 pytest 用例中运行异步来源适配器。
import json  # 加载本地 DBLP 出版物 fixture。
from pathlib import Path  # 定位测试 fixture 文件。

import httpx  # 使用 MockTransport 拦截 HTTP 请求。
import pytest  # 提供异常断言工具。

from backend.app.adapters.base import AcademicSearchAdapter  # 验证客户端满足统一来源协议。
from backend.app.adapters.dblp import DblpClient, DblpClientError, build_dblp_search_params, select_dblp_primary_topic  # 导入待测客户端、异常和宽松主题参数构造器。
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
    return Settings(_env_file=None, dblp_requests_per_second=5, academic_api_max_retries=0)  # 提升测试频率并避免错误边界测试真实等待。


def test_search_params_map_query_intent_to_official_fields() -> None:
    """参数构造器应仅将宽泛研究主题映射为 DBLP 的 q、format、h 与 f 参数。"""
    params = build_dblp_search_params(_build_query_intent())  # 构造不含网络或密钥的来源参数。
    assert params == {"q": "forecasting", "format": "json", "h": 5, "f": 0, "c": 0}  # 验证方法 Transformer 不会因 DBLP 空格 AND 语义而收窄主题召回。


def test_dblp_uses_one_broad_primary_topic_and_does_not_merge_constraints() -> None:
    """DBLP 只能使用一个宽泛主要主题，不能把空格解释为全部条件的 AND。"""
    query = QueryIntent(  # 构造包含所有后续条件的完整意图。
        original_query="检索时间序列基础模型",  # 提供不会进入 DBLP q 的原始问题。
        normalized_query="foundation models for forecasting",  # 仅在主题缺失时可回退。
        query_language="mixed",  # 标记输入语言。
        research_topics=["time series foundation models", "large language models for forecasting"],  # 提供两个可构成宽泛主任务的研究主题。
        methods=["pretrained models"],  # 提供不得进入 q 的方法。
        tasks=["zero-shot forecasting"],  # 提供不得进入 q 的任务。
        datasets=["ETT"],  # 提供不得进入 q 的数据集。
        must_include=["cross-domain generalization"],  # 提供不得进入 q 的硬关键词。
        should_include=["open source"],  # 提供仅后续排序使用的软偏好。
        exclude=["survey"],  # 提供仅后续过滤使用的排除条件。
        year_range=(2022, 2026),  # 提供不得进入 q 的年份范围。
    )
    original_dump = query.model_dump()  # 保存调用前完整意图快照。

    params = build_dblp_search_params(query)  # 生成不访问网络的 DBLP 单页参数。

    assert select_dblp_primary_topic(query) == "time series forecasting" and params["q"] == "time series forecasting"  # 验证从研究主题中保守选择宽泛主任务，而非拼接全部字段。
    assert all(term not in params["q"] for term in ["pretrained", "zero-shot", "ETT", "cross-domain", "open source", "survey", "2022", "2026"])  # 验证约束和年份不进入 DBLP q。
    assert params["format"] == "json" and params["h"] == query.target_paper_count and params["f"] == 0 and params["c"] == 0  # 验证官方分页、格式和自动补全参数保持不变。
    assert query.model_dump() == original_dump  # 验证来源宽召回不会修改后续过滤、排序和核验所需约束。


def test_dblp_falls_back_to_normalized_query_only_when_research_topics_are_empty() -> None:
    """仅在没有有效研究主题时，DBLP 才可使用规范化查询保持来源可调用。"""
    query = QueryIntent(original_query="检索预测论文", normalized_query="time series forecasting", query_language="mixed", research_topics=["  "], methods=["Transformer"])  # 构造主题为空但方法存在的回退边界。

    params = build_dblp_search_params(query)  # 构造不访问网络的来源参数。

    assert params["q"] == "time series forecasting" and "Transformer" not in params["q"]  # 验证回退只读取 normalized_query，方法不会替代研究主题。


def test_client_implements_unified_adapter_and_maps_search_response() -> None:
    """客户端应满足协议、调用出版物端点并映射带来源溯源的论文记录。"""
    fixture = _load_dblp_payload_fixture()  # 读取本地 JSON 响应。

    def handler(request: httpx.Request) -> httpx.Response:
        """校验请求参数并返回本地成功响应。"""
        assert request.url.path == "/search/publ/api"  # 验证基地址路径与出版物搜索端点被正确拼接。
        assert request.url.params["q"] == "forecasting"  # 验证 DBLP 只接收一个宽泛研究主题。
        assert request.url.params["h"] == "5"  # 验证目标结果数量映射为来源最大命中数。
        assert request.url.params["format"] == "json"  # 验证客户端显式请求官方 JSON 响应格式。
        assert request.url.params["c"] == "0"  # 验证后端搜索关闭不会消费的来源自动补全计算。
        return httpx.Response(200, json=fixture, request=request)  # 返回不依赖网络的 DBLP 响应。

    client = DblpClient(  # 使用 mock 传输层构造统一来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    assert isinstance(client, AcademicSearchAdapter)  # 验证客户端满足统一来源适配器协议。
    papers = asyncio.run(client.search(_build_query_intent()))  # 执行不访问网络的异步单页搜索。
    assert papers[0].dblp_key == "conf/aaai/Lovelace25"  # 验证来源稳定 DBLP 键被保留。
    assert papers[0].source_records[0].raw_rank == 1  # 验证首条结果写入 RRF 所需的来源排名。


def test_client_filters_dblp_papers_by_year_range_after_mapping_without_second_request() -> None:
    """DBLP 不在 q 中传年份，映射后的论文必须在本地按 QueryIntent 年份过滤。"""
    request_count = 0  # 记录来源请求次数以防止年份过滤错误地触发第二次调用。
    payload = {  # 构造两个可映射但年份不同的离线 DBLP 命中。
        "result": {  # 保持官方顶层 result 包装。
            "hits": {  # 保持官方 hits 包装。
                "hit": [  # 提供范围内和范围外两条论文。
                    {"info": {"key": "conf/test/in-range", "title": "In range", "year": "2024", "type": "Conference"}},  # 2024 应保留。
                    {"info": {"key": "conf/test/out-of-range", "title": "Out of range", "year": "2020", "type": "Conference"}},  # 2020 应在本地移除。
                ]
            }
        }
    }
    query = QueryIntent(original_query="检索预测论文", normalized_query="time series forecasting", query_language="mixed", research_topics=["time series forecasting"], methods=["Transformer"], year_range=(2022, 2026))  # 提供来源主题、后续方法和本地年份硬条件。

    def handler(request: httpx.Request) -> httpx.Response:
        """断言年份未进入 q，并返回一次本地 DBLP JSON 响应。"""
        nonlocal request_count  # 更新闭包中的来源调用计数。
        request_count += 1  # 记录单页来源请求。
        assert request.url.params["q"] == "time series forecasting" and "2022" not in request.url.params["q"]  # 验证年份不被拼入 DBLP q。
        return httpx.Response(200, json=payload, request=request)  # 返回离线成功响应，不访问真实 DBLP。

    client = DblpClient(settings_override=_build_test_settings(), transport=httpx.MockTransport(handler))  # 注入离线传输层和无等待测试配置。
    papers = asyncio.run(client.search(query))  # 执行一次来源请求与映射后的本地年份过滤。

    assert request_count == 1 and [paper.dblp_key for paper in papers] == ["conf/test/in-range"]  # 验证不增加网络请求且仅返回范围内的映射论文。


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
