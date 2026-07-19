"""验证 OpenAlex 对统一来源协议、查询意图与来源溯源的支持。"""

import asyncio  # 在同步 pytest 用例中运行异步来源适配器。
import json  # 加载本地 OpenAlex Work fixture。
from pathlib import Path  # 定位测试 fixture 文件。

import httpx  # 使用 MockTransport 拦截 HTTP 请求。

from backend.app.adapters.base import AcademicSearchAdapter  # 验证客户端满足统一来源协议。
from backend.app.adapters.openalex import OpenAlexClient, build_openalex_search_params  # 导入待测统一入口与纯参数构造器。
from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离配置。
from backend.app.models.query_intent import QueryIntent  # 构造统一来源协议要求的查询输入。


def _load_openalex_work_fixture() -> dict[str, object]:
    """读取固定的 OpenAlex Work 响应样例。

    返回：
        dict[str, object]：不依赖网络的单条 Work fixture。
    """
    fixture_path = Path(__file__).parent / "fixtures" / "openalex_work.json"  # 根据测试文件位置构造 fixture 路径。
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # 使用 UTF-8 解码并解析 JSON 数据。


def _build_query_intent() -> QueryIntent:
    """构造可被 OpenAlex 统一入口消费的最小有效查询意图。

    返回：
        QueryIntent：包含主题、方法、年份与目标数量的离线测试意图。
    """
    return QueryIntent(  # 构造无需 LLM 或网络的查询规划结果。
        original_query="Transformer forecasting after 2020",  # 提供用户原始查询文本。
        normalized_query="Transformer forecasting",  # 提供可复现的规范化查询文本。
        query_language="en",  # 标记查询语言。
        research_topics=["forecasting"],  # 提供主题检索词。
        methods=["Transformer"],  # 提供方法检索词。
        year_range=(2020, 2024),  # 提供来源可执行的发表年份范围。
        target_paper_count=5,  # 限制测试请求规模。
    )


def _build_test_settings() -> Settings:
    """构造不读取真实 .env 的 OpenAlex 测试配置。

    返回：
        Settings：携带无实际权限测试密钥的隔离设置。
    """
    return Settings(_env_file=None, openalex_api_key="test-api-key")  # 注入仅用于请求断言的虚拟密钥。


def test_search_params_fall_back_to_normalized_query() -> None:
    """未拆分出检索词的意图应回退为规范化查询而非构造空搜索。"""
    query = QueryIntent(  # 构造仅包含规范化查询的有效意图。
        original_query="复杂问题",  # 提供最小原始查询文本。
        normalized_query="复杂问题",  # 提供来源搜索所需的回退文本。
        query_language="zh",  # 标记中文查询。
    )
    params = build_openalex_search_params(query)  # 构造不含网络或密钥的来源参数。
    assert params["search"] == "复杂问题"  # 验证不会向来源发送空搜索参数。
    assert "sort" not in params  # 验证统一 QueryIntent 入口复用 OpenAlex 搜索默认相关性顺序，不添加冗余排序参数。
    assert "filter" not in params  # 验证未指定年份范围时不会隐式加入来源过滤条件。


def test_search_params_normalize_pasa_style_apostrophes_and_question_marks_only_for_openalex() -> None:
    """QueryIntent 原文保持不变时，OpenAlex 请求文本应确定性兼容 PaSa 的智能标点。"""
    original_query = "Who projected the first method for distinguishing the neurons’ ability based on the neuron’s activation value?"  # 固定复现 PaSa 开发集的实际来源兼容性边界。
    query = QueryIntent(original_query=original_query, normalized_query=original_query, query_language="en")  # 保持 QueryIntent 中原始与规范化查询均未被来源适配器改写。
    params = build_openalex_search_params(query)  # 构造不访问网络的 OpenAlex 参数。
    assert query.original_query == original_query and query.normalized_query == original_query  # 验证领域契约和评测输入未被此来源规范化函数回写。
    assert params["search"] == "Who projected the first method for distinguishing the neurons ability based on the neurons activation value"  # 验证仅删除撇号、替换问号并压缩空白。


def test_client_implements_unified_adapter_and_maps_provenance() -> None:
    """统一入口应满足协议、转换 QueryIntent 并保留 OpenAlex 来源排名。"""
    fixture = _load_openalex_work_fixture()  # 读取本地 Work 响应。

    def handler(request: httpx.Request) -> httpx.Response:
        """校验统一入口请求参数并返回本地成功响应。"""
        assert request.url.path == "/works"  # 验证客户端调用 OpenAlex 论文搜索端点。
        assert request.url.params["search"] == "forecasting Transformer"  # 验证查询意图按确定顺序映射为全文搜索词。
        assert "sort" not in request.url.params  # 验证网络请求复用 OpenAlex 搜索默认相关性顺序，不发送冗余排序参数。
        assert "select" not in request.url.params  # 验证网络请求接收完整 Work 响应，避免选择字段与来源版本不兼容。
        assert request.url.params["per_page"] == "5"  # 验证目标结果数量映射为来源单页限制。
        assert request.url.params["filter"] == "publication_year:2020-2024"  # 验证年份范围映射为来源过滤。
        return httpx.Response(200, json={"results": [fixture]}, request=request)  # 返回不依赖网络的 OpenAlex 响应。

    client = OpenAlexClient(  # 使用 mock 传输层构造统一来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
    )
    assert isinstance(client, AcademicSearchAdapter)  # 验证客户端满足统一来源适配器协议。
    papers = asyncio.run(client.search(_build_query_intent()))  # 执行不访问网络的统一搜索入口。
    assert papers[0].openalex_id == "https://openalex.org/W1234567890"  # 验证 OpenAlex 主标识被显式保留。
    assert papers[0].source_records[0].raw_rank == 1  # 验证首条结果写入 RRF 所需的来源排名。
    assert papers[0].authors[0].source_author_ids["openalex"] == "https://openalex.org/A1234567890"  # 验证来源作者标识被保留。
