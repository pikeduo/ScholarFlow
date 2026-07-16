"""验证 Semantic Scholar 客户端的离线请求、协议和错误边界。"""

import asyncio  # 在同步 pytest 用例中运行异步来源适配器。
import json  # 加载本地 Semantic Scholar JSON fixture。
from pathlib import Path  # 定位测试 fixture 文件。
from unittest.mock import AsyncMock, patch  # 跳过限流重试的真实等待并验证异步调用。

import httpx  # 使用 MockTransport 拦截 HTTP 请求。
import pytest  # 提供异常断言工具。

from backend.app.adapters.base import AcademicSearchAdapter  # 验证客户端满足统一来源协议。
from backend.app.adapters.academic_api import AcademicApiRequestExecutor  # 注入不真实等待的统一重试执行器。
from backend.app.adapters.semantic_scholar import SemanticScholarClient, SemanticScholarClientError  # 导入待测客户端和领域异常。
from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离配置。
from backend.app.models.query_intent import QueryIntent  # 构造统一适配器输入。
from backend.app.repositories.source_rate_limiter import SourceCooldownError  # 构造其他进程已进入冷却的来源限流替身。


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


class RemoteCooldownRateLimiter:
    """模拟其他进程已经收到 429 并写入共享冷却状态。"""

    async def acquire(self, source: str, requests_per_second: float) -> bool:
        """始终阻止当前进程继续访问已冷却来源。"""
        raise SourceCooldownError("semantic_scholar 当前处于跨进程冷却期")  # 模拟 Redis 冷却键命中。

    async def penalize(self, source: str, cooldown_seconds: float) -> bool:
        """保留限流器接口完整性，当前用例不会触发该方法。"""
        return True  # 模拟冷却已由其他进程同步。


class CooldownRecordingRateLimiter:
    """记录客户端写入的 429 冷却秒数，避免测试依赖真实 Redis。"""

    def __init__(self) -> None:
        """初始化可观测的来源冷却记录。"""
        self.penalized: list[tuple[str, float]] = []  # 保存每次 429 触发的来源和冷却秒数。

    async def acquire(self, _: str, __: float) -> bool:
        """模拟未使用 Redis 共享窗口时的进程内限流回退。"""
        return False  # 允许测试请求继续进入本地 MockTransport。

    async def penalize(self, source: str, cooldown_seconds: float) -> bool:
        """记录调用方要求同步的固定冷却时长。"""
        self.penalized.append((source, cooldown_seconds))  # 保存断言所需的来源冷却事实。
        return True  # 模拟 Redis 已成功同步冷却键。


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
    return Settings(_env_file=None, semantic_scholar_api_key=api_key, academic_api_max_retries=0)  # 注入测试密钥并默认关闭错误边界测试的重试等待。


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
    request_count = 0  # 统计限流响应后的真实外部调用次数。

    def handler(request: httpx.Request) -> httpx.Response:
        """返回模拟来源限流响应。"""
        nonlocal request_count  # 更新当前用例调用计数。
        request_count += 1  # 记录实际进入 MockTransport 的请求。
        return httpx.Response(429, headers={"Retry-After": "90"}, request=request)  # 模拟带较长供应商建议的限流响应。

    rate_limiter = CooldownRecordingRateLimiter()  # 注入可验证固定冷却时长的 Redis 限流替身。
    client = SemanticScholarClient(  # 使用 mock 限流响应构造来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离配置。
        transport=httpx.MockTransport(handler),  # 拦截真实网络访问。
        source_rate_limiter=rate_limiter,  # 防止默认全局 Redis 状态影响此离线断言。
    )
    async def execute_twice() -> None:
        """在同一事件循环验证首次 429 后快速降级。"""
        with pytest.raises(SemanticScholarClientError, match="HTTP 429"):  # 首次调用应返回净化状态错误。
            await client.search(_build_query_intent())  # 发起唯一一次外部请求并触发冷却。
        with pytest.raises(SemanticScholarClientError, match="冷却期"):  # 冷却期内不应再次访问来源。
            await client.search(_build_query_intent())  # 验证进程内快速降级。

    with patch("backend.app.adapters.academic_api.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:  # 验证零重试配置不会触发共享执行器等待。
        asyncio.run(execute_twice())  # 执行同一事件循环内的两次搜索。
    assert request_count == 1  # 验证零重试配置会在首次最终 429 后立即进入统一冷却。
    sleep_mock.assert_not_awaited()  # 验证最终失败不会再无意义等待。
    assert rate_limiter.penalized == [("semantic_scholar", 90.0)]  # 验证更长 Retry-After 会扩展来源级冷却。


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
    """供应商以 HTTP 200 返回限流信封时客户端应按统一退避在预算内重试。"""
    fixture = _load_semantic_scholar_paper_fixture()  # 读取重试成功时返回的本地论文样例。
    request_count = 0  # 统计 MockTransport 收到的调用次数。

    def handler(request: httpx.Request) -> httpx.Response:
        """首次返回限流信封，第二次返回正常 data。"""
        nonlocal request_count  # 更新当前用例的调用计数。
        request_count += 1  # 记录本次来源请求。
        payload = {"message": "Too many requests 429"} if request_count == 1 else {"data": [fixture]}  # 按次数切换响应。
        return httpx.Response(200, json=payload, request=request)  # 保持完全离线的成功状态响应。

    settings = Settings(_env_file=None, semantic_scholar_api_key="test-api-key", academic_api_max_retries=1)  # 允许一次统一限流重试。
    sleep_mock = AsyncMock()  # 记录等待参数而不真实等待。
    executor = AcademicApiRequestExecutor("semantic_scholar", settings, settings.semantic_scholar_requests_per_second, retry_sleep=sleep_mock, rate_limit_sleep=AsyncMock(), random_uniform=lambda _start, _end: 0.0)  # 仅记录退避等待，避免同一 mock 接收 RPS 等待。
    client = SemanticScholarClient(settings_override=settings, transport=httpx.MockTransport(handler), request_executor=executor)  # 注入离线来源响应与可观测执行器。
    papers = asyncio.run(client.search(_build_query_intent()))  # 执行限流后成功路径。

    assert request_count == 2  # 验证只发起首次调用和一次重试。
    assert [paper.paper_id for paper in papers] == ["S2-paper-123"]  # 验证重试成功结果正常映射。
    sleep_mock.assert_awaited_once_with(15.0)  # 验证非标准限流信封同样遵循统一指数退避。


def test_client_retries_http_rate_limit_after_three_seconds() -> None:
    """HTTP 429 后应按统一退避补发一次，并使用第二次成功结果。"""
    fixture = _load_semantic_scholar_paper_fixture()  # 读取第二次请求成功时返回的离线论文样例。
    request_count = 0  # 统计同一请求的首次限流和一次补发调用。

    def handler(request: httpx.Request) -> httpx.Response:
        """首次模拟 429，第二次返回可映射的正常论文数组。"""
        nonlocal request_count  # 更新当前闭包内的来源请求次数。
        request_count += 1  # 记录实际进入 HTTP 适配器的每次调用。
        if request_count == 1:  # 首次调用模拟供应商暂时限流。
            return httpx.Response(429, request=request)  # 触发固定三秒的补发路径。
        return httpx.Response(200, json={"data": [fixture]}, request=request)  # 第二次调用模拟供应商恢复可用。

    retry_settings = Settings(_env_file=None, semantic_scholar_api_key="test-api-key", academic_api_max_retries=1)  # 为该用例显式保留一次统一重试预算。
    sleep_mock = AsyncMock()  # 记录等待参数而不真实等待。
    executor = AcademicApiRequestExecutor("semantic_scholar", retry_settings, retry_settings.semantic_scholar_requests_per_second, retry_sleep=sleep_mock, rate_limit_sleep=AsyncMock(), random_uniform=lambda _start, _end: 0.0)  # 仅记录退避等待，避免同一 mock 接收 RPS 等待。
    client = SemanticScholarClient(settings_override=retry_settings, transport=httpx.MockTransport(handler), request_executor=executor)  # 使用一次统一重试预算验证恢复行为。
    papers = asyncio.run(client.search(_build_query_intent()))  # 执行首次 429 后补发成功的完整搜索。

    assert request_count == 2  # 验证只执行首次调用和一次补发，未无限重试。
    assert [paper.paper_id for paper in papers] == ["S2-paper-123"]  # 验证补发成功结果仍完成统一论文映射。
    sleep_mock.assert_awaited_once_with(15.0)  # 验证 HTTP 429 的补发遵循统一指数退避。


def test_client_uses_cached_response_before_cooldown_or_second_network_request() -> None:
    """同一 Semantic Scholar 查询应优先命中缓存，不再触发来源限流等待。"""
    fixture = _load_semantic_scholar_paper_fixture()  # 读取本地论文响应样例。
    request_count = 0  # 统计实际进入来源 HTTP 传输层的次数。

    def handler(request: httpx.Request) -> httpx.Response:
        """记录来源调用并返回固定成功响应。"""
        nonlocal request_count  # 更新当前测试闭包中的调用计数。
        request_count += 1  # 记录一次真实 HTTP 适配器调用。
        return httpx.Response(200, json={"data": [fixture]}, request=request)  # 返回离线固定成功响应。

    client = SemanticScholarClient(  # 构造带内存缓存替身的来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离来源配置。
        transport=httpx.MockTransport(handler),  # 阻止真实网络访问。
        response_cache=FakeResponseCache(),  # 注入可观察缓存命中的替身。
    )
    query = _build_query_intent()  # 构造两次完全相同的统一查询意图。
    asyncio.run(client.search(query))  # 首次调用应回源并写入缓存。
    asyncio.run(client.search(query))  # 第二次调用应直接读取缓存。
    assert request_count == 1  # 验证缓存命中没有额外消耗 Semantic Scholar 调用次数。


def test_client_skips_network_when_remote_rate_limiter_reports_cooldown() -> None:
    """Redis 同步的来源冷却应在 HTTP 调用前转换为既有稳定冷却错误。"""
    request_count = 0  # 统计不应发生的 HTTP 来源调用。

    def handler(request: httpx.Request) -> httpx.Response:
        """记录异常的网络调用，若被调用则测试失败。"""
        nonlocal request_count  # 更新当前测试闭包中的调用计数。
        request_count += 1  # 记录意外进入来源传输层的调用。
        return httpx.Response(500, request=request)  # 返回无关响应，正常路径不应触达。

    client = SemanticScholarClient(  # 构造注入远程冷却替身的来源客户端。
        settings_override=_build_test_settings(),  # 注入隔离来源配置。
        transport=httpx.MockTransport(handler),  # 阻止真实网络访问。
        source_rate_limiter=RemoteCooldownRateLimiter(),  # 模拟其他进程写入 Redis 冷却状态。
    )
    with pytest.raises(SemanticScholarClientError, match="冷却期"):  # 验证对协调器保持既有稳定错误契约。
        asyncio.run(client.search(_build_query_intent()))  # 应在 HTTP 层前停止。
    assert request_count == 0  # 验证共享冷却没有消耗 Semantic Scholar API 调用。
