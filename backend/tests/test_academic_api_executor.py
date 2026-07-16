"""验证统一学术 API 重试、等待、冷却与安全日志，不访问真实网络。"""

import asyncio  # 在同步 pytest 用例中驱动异步共享执行器。
from datetime import UTC, datetime  # 固定 HTTP 日期 Retry-After 的解析基准。

import httpx  # 使用本地 Response 和网络异常替身。
import pytest  # 提供异常和日志断言工具。

from backend.app.adapters.academic_api import AcademicApiNetworkError, AcademicApiRequestExecutor, parse_retry_after  # 导入共享执行器与纯解析函数。
from backend.app.core.config import Settings  # 构造不读取用户本地环境的隔离策略。
from backend.app.repositories.source_rate_limiter import SourceCooldownError  # 验证本地冷却的稳定快速失败边界。


class RecordingRateLimiter:
    """记录共享执行器的 RPS 占用与 Redis 冷却同步调用。"""

    def __init__(self, fail: bool = False) -> None:
        """保存可选的 Redis 同步失败开关和可观测调用记录。"""
        self.fail = fail  # 为 Redis 不可用回退路径提供测试开关。
        self.acquired: list[tuple[str, float]] = []  # 记录每次尝试是否重新经过来源窗口。
        self.penalized: list[tuple[str, float]] = []  # 记录最终 429 写入的来源冷却。

    async def acquire(self, source: str, requests_per_second: float) -> bool:
        """模拟可用或不可用 Redis 下均不阻断进程内执行器。"""
        self.acquired.append((source, requests_per_second))  # 保留所有首次与重试请求的窗口事实。
        return not self.fail  # Redis 失败时真实限流器同样回退到本地路径。

    async def penalize(self, source: str, cooldown_seconds: float) -> bool:
        """记录冷却同步请求，模拟 Redis 可用性结果。"""
        self.penalized.append((source, cooldown_seconds))  # 即使 Redis 不可用，本地冷却也应已激活。
        return not self.fail  # 返回值仅影响日志，不应覆盖本地冷却。


def _settings(**overrides: object) -> Settings:
    """构造稳定、无真实环境依赖的统一策略设置。"""
    return Settings(_env_file=None, **overrides)  # 仅使用本测试模块显式给出的非敏感参数。


def _response(status_code: int, headers: dict[str, str] | None = None, text: str = "") -> httpx.Response:
    """构造携带本地请求对象的 HTTP 响应，避免 MockTransport 以外的网络访问。"""
    return httpx.Response(status_code, headers=headers, text=text, request=httpx.Request("GET", "https://academic.test/search"))  # 使用测试域名且不含任何认证参数。


def test_retry_after_parses_numeric_http_date_invalid_and_capped_values() -> None:
    """Retry-After 应支持两种官方格式、过去日期、非法值和安全上限。"""
    now = datetime(2026, 10, 21, 7, 27, 30, tzinfo=UTC)  # 固定时间使日期解析完全确定。
    assert parse_retry_after("30", now=now, maximum_seconds=300) == 30.0  # 验证整数秒格式。
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", now=now, maximum_seconds=300) == 30.0  # 验证 HTTP 日期格式。
    assert parse_retry_after("Wed, 21 Oct 2026 07:00:00 GMT", now=now, maximum_seconds=300) == 0.0  # 验证过去日期按最小等待处理。
    assert parse_retry_after("not-a-date", now=now, maximum_seconds=300) is None  # 验证非法值安全回退。
    assert parse_retry_after("9999", now=now, maximum_seconds=300) == 300.0  # 验证超长等待被上限截断。


def test_429_retry_after_uses_supplier_wait_and_rechecks_rate_limit() -> None:
    """数字 Retry-After 应优先于退避，且重试前必须重新占用来源窗口。"""
    sleeps: list[float] = []  # 记录注入等待而不真实休眠。
    limiter = RecordingRateLimiter()  # 记录第一次与重试前的来源窗口获取。
    settings = _settings(academic_api_max_retries=1, academic_api_jitter_max_seconds=3)  # 保留一次重试并验证 jitter。
    executor = AcademicApiRequestExecutor("openalex", settings, 5.0, source_rate_limiter=limiter, retry_sleep=lambda seconds: _record_sleep(sleeps, seconds), rate_limit_sleep=lambda _seconds: _completed(), random_uniform=lambda _start, _end: 1.25)  # 将重试与 RPS 等待独立注入，避免断言混淆。
    responses = iter([_response(429, {"Retry-After": "30"}), _response(200)])  # 首次限流、第二次恢复成功。

    result = asyncio.run(executor.execute(lambda: _next_response(responses)))  # 执行两次离线 HTTP 尝试。

    assert result.status_code == 200  # 验证恢复后正常返回成功响应。
    assert sleeps == [31.25]  # 验证供应商等待优先且加入配置范围内 jitter。
    assert len(limiter.acquired) == 2  # 验证重试没有绕过来源 RPS/Redis 窗口。


def test_default_exponential_backoff_sequence_and_503_retry() -> None:
    """缺失 Retry-After 的临时 503 应按 15、30、60 秒重试。"""
    sleeps: list[float] = []  # 记录三次指数等待。
    settings = _settings(academic_api_max_retries=3, academic_api_jitter_max_seconds=0)  # 使用默认退避并关闭抖动便于精确断言。
    executor = AcademicApiRequestExecutor("dblp", settings, 5.0, source_rate_limiter=RecordingRateLimiter(), retry_sleep=lambda seconds: _record_sleep(sleeps, seconds), rate_limit_sleep=lambda _seconds: _completed(), random_uniform=lambda _start, _end: 0.0)  # 将退避等待与正常 RPS 等待独立隔离。
    responses = iter([_response(503), _response(503), _response(503), _response(200)])  # 第四次请求成功。

    assert asyncio.run(executor.execute(lambda: _next_response(responses))).status_code == 200  # 验证 503 属于可恢复错误。
    assert sleeps == [15.0, 30.0, 60.0]  # 验证默认指数退避序列。


@pytest.mark.parametrize("status_code", [401, 403])
def test_authentication_and_permission_errors_are_not_retried(status_code: int) -> None:
    """401 与 403 必须立即返回给适配器，不应浪费来源配额。"""
    limiter = RecordingRateLimiter()  # 记录请求次数。
    executor = AcademicApiRequestExecutor("semantic_scholar", _settings(academic_api_max_retries=3), 1.0, source_rate_limiter=limiter, retry_sleep=lambda _seconds: _completed(), rate_limit_sleep=lambda _seconds: _completed())  # 注入不可等待的执行器。

    assert asyncio.run(executor.execute(lambda: _completed_response(_response(status_code)))).status_code == status_code  # 验证状态原样返回以保留适配器领域映射。
    assert len(limiter.acquired) == 1  # 验证认证与权限错误不会重试。


def test_network_timeout_is_retried_without_real_wait() -> None:
    """读取超时属于临时网络错误，应按统一预算重试。"""
    sleeps: list[float] = []  # 记录网络错误使用的退避等待。
    request = httpx.Request("GET", "https://academic.test/search")  # 构造 httpx 超时异常需要的请求对象。
    attempts = 0  # 控制首次超时、第二次成功。

    async def request_call() -> httpx.Response:
        """模拟一次 ReadTimeout 后恢复成功的幂等 GET。"""
        nonlocal attempts  # 更新闭包中的尝试计数。
        attempts += 1  # 记录调用次数。
        if attempts == 1:  # 首次模拟临时网络超时。
            raise httpx.ReadTimeout("timeout", request=request)  # 触发统一网络重试。
        return _response(200)  # 第二次成功。

    executor = AcademicApiRequestExecutor("pubmed", _settings(academic_api_max_retries=1, academic_api_jitter_max_seconds=0), 10.0, source_rate_limiter=RecordingRateLimiter(), retry_sleep=lambda seconds: _record_sleep(sleeps, seconds), rate_limit_sleep=lambda _seconds: _completed(), random_uniform=lambda _start, _end: 0.0)  # 注入离线策略依赖。
    assert asyncio.run(executor.execute(request_call)).status_code == 200  # 验证恢复后可正常返回。
    assert sleeps == [15.0]  # 验证网络错误同样走指数退避。


def test_final_429_activates_local_and_redis_cooldown_without_second_transport_call() -> None:
    """最终 429 应同步 Redis、保留本地冷却，并阻断后续 HTTP 请求。"""
    limiter = RecordingRateLimiter()  # 记录 Redis 冷却同步。
    transport_calls = 0  # 记录不应发生的第二次 HTTP 调用。

    async def rate_limited_request() -> httpx.Response:
        """返回最终限流响应并记录唯一 HTTP 调用。"""
        nonlocal transport_calls  # 更新传输调用计数。
        transport_calls += 1  # 记录本次实际请求。
        return _response(429, {"Retry-After": "90"})  # 提供比默认冷却更长的供应商建议。

    executor = AcademicApiRequestExecutor("arxiv", _settings(academic_api_max_retries=0), 1.0, source_rate_limiter=limiter, retry_sleep=lambda _seconds: _completed(), rate_limit_sleep=lambda _seconds: _completed())  # 立即耗尽重试预算。
    assert asyncio.run(executor.execute(rate_limited_request)).status_code == 429  # 验证最终 HTTP 状态仍交给适配器映射。
    assert limiter.penalized == [("arxiv", 90.0)]  # 验证更长 Retry-After 优先于默认 60 秒冷却。
    with pytest.raises(SourceCooldownError):  # 验证当前进程冷却期内直接失败。
        asyncio.run(executor.execute(rate_limited_request))  # 不得再次访问传输层。
    assert transport_calls == 1  # 验证冷却期没有第二次 HTTP transport 调用。


def test_redis_failure_keeps_local_cooldown_and_other_sources_isolated() -> None:
    """Redis 不可用时仍须本地冷却，且一个来源失败不应阻断其他来源。"""
    failing_executor = AcademicApiRequestExecutor("semantic_scholar", _settings(academic_api_max_retries=0), 1.0, source_rate_limiter=RecordingRateLimiter(fail=True), retry_sleep=lambda _seconds: _completed(), rate_limit_sleep=lambda _seconds: _completed())  # 模拟 Redis 回退路径。
    healthy_executor = AcademicApiRequestExecutor("openalex", _settings(academic_api_max_retries=0), 5.0, source_rate_limiter=RecordingRateLimiter(fail=True), retry_sleep=lambda _seconds: _completed(), rate_limit_sleep=lambda _seconds: _completed())  # 使用不同来源独立执行器。

    assert asyncio.run(failing_executor.execute(lambda: _completed_response(_response(429)))).status_code == 429  # 触发失败来源本地冷却。
    with pytest.raises(SourceCooldownError):  # 验证 Redis 不可用时仍保留本地冷却。
        asyncio.run(failing_executor.execute(lambda: _completed_response(_response(200))))  # 不得再次调用受限来源。
    assert asyncio.run(healthy_executor.execute(lambda: _completed_response(_response(200)))).status_code == 200  # 验证其他来源不受影响。


def test_success_has_no_extra_retry_and_safe_log_does_not_leak_sensitive_content(caplog: pytest.LogCaptureFixture) -> None:
    """成功响应不应重试，统一日志不得包含 URL 查询、密钥或响应正文。"""
    secret_body = "super-secret-response-body"  # 构造禁止出现在日志中的上游内容。
    executor = AcademicApiRequestExecutor("dblp", _settings(academic_api_max_retries=1), 5.0, source_rate_limiter=RecordingRateLimiter(), retry_sleep=lambda _seconds: _completed(), rate_limit_sleep=lambda _seconds: _completed())  # 构造离线执行器。

    assert asyncio.run(executor.execute(lambda: _completed_response(_response(200, text=secret_body)))).status_code == 200  # 验证成功响应直接返回。
    messages = "\n".join(record.getMessage() for record in caplog.records)  # 仅读取结构化日志消息供泄露断言。
    assert "super-secret-response-body" not in messages  # 验证响应正文未被写入日志。
    assert "https://academic.test/search" not in messages  # 验证完整请求 URL 未被写入日志。


async def _record_sleep(values: list[float], seconds: float) -> None:
    """记录共享执行器请求的等待秒数而不实际休眠。"""
    values.append(seconds)  # 保留可精确断言的策略结果。


async def _next_response(responses: object) -> httpx.Response:
    """从预先定义的响应迭代器中取得下一次离线 HTTP 响应。"""
    return next(responses)  # 迭代耗尽即代表测试的请求次数超出预期。


async def _completed_response(response: httpx.Response) -> httpx.Response:
    """将已构造响应包装为异步请求回调结果。"""
    return response  # 适配共享执行器的异步请求接口。


async def _completed() -> None:
    """提供不产生真实等待的空异步 sleep 替身。"""
    return None  # 测试只验证传参，不等待真实时间。
