"""提供所有幂等学术来源 HTTP 调用共用的重试、限流与冷却执行器。"""

import asyncio  # 在不阻塞事件循环的前提下实施来源级等待。
import random  # 为重试等待加入可注入的随机抖动。
from collections.abc import Awaitable, Callable  # 声明可注入请求、等待和时间边界。
from datetime import UTC, datetime  # 解析 HTTP 日期格式的 Retry-After。
from email.utils import parsedate_to_datetime  # 复用标准库处理官方 HTTP 日期。

import httpx  # 识别可重试的 HTTP 与网络错误。

from backend.app.core.config import Settings  # 读取集中配置的重试和冷却策略。
from backend.app.core.logging import logger  # 记录不含 URL、查询、认证头或正文的统一日志。
from backend.app.repositories.source_rate_limiter import SourceCooldownError, SourceRateLimiter, get_source_rate_limiter  # 复用 Redis 跨进程窗口与冷却状态。


RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})  # 仅重试明确具有临时性的 HTTP 状态。


class AcademicApiNetworkError(RuntimeError):
    """表示学术来源在耗尽网络重试预算后仍不可达。"""


def parse_retry_after(value: str | None, *, now: datetime, maximum_seconds: float) -> float | None:
    """解析 Retry-After 的秒数或 HTTP 日期，并将结果限制在安全上限内。

    参数：
        value：响应头中可选的 Retry-After 原始值。
        now：可注入的当前 UTC 时间，便于稳定测试 HTTP 日期格式。
        maximum_seconds：供应商等待时间允许的最大秒数。
    返回：
        float | None：有效且已截断的等待秒数；非法值返回空值。
    """
    if not value:  # 缺失头时交由调用方回退到指数退避。
        return None  # 不把缺失视为异常。
    normalized = value.strip()  # 忽略供应商响应头两侧的可见空白。
    if not normalized:  # 空白值同样不能表示有效等待。
        return None  # 安全回退到默认策略。
    try:  # 优先按官方整数秒格式解析。
        seconds = float(int(normalized))  # 拒绝小数和符号混乱的非标准值。
    except ValueError:  # 整数格式不匹配时再尝试官方 HTTP 日期。
        try:  # HTTP 日期可能携带时区或 UTC 标识。
            parsed = parsedate_to_datetime(normalized)  # 标准库会拒绝非法日期。
        except (TypeError, ValueError, IndexError, OverflowError):  # 不可信响应头绝不能中断来源降级。
            return None  # 安全回退到指数退避。
        if parsed.tzinfo is None:  # 极少数供应商可能遗漏时区。
            parsed = parsed.replace(tzinfo=UTC)  # 按 HTTP 规范将其保守视为 UTC。
        seconds = max(0.0, (parsed.astimezone(UTC) - now.astimezone(UTC)).total_seconds())  # 过去日期按最小零秒等待。
    return min(max(seconds, 0.0), maximum_seconds)  # 限制异常供应商值避免无界等待。


class AcademicApiRequestExecutor:
    """为一个学术来源执行幂等 HTTP 请求，并统一处理 RPS、重试与冷却。

    适配器仅传入 GET 请求回调及来源特有的 RPS；执行器不理解供应商 URL、认证或响应字段。
    """

    def __init__(
        self,
        source: str,
        settings: Settings,
        requests_per_second: float,
        *,
        source_rate_limiter: SourceRateLimiter | None = None,
        retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rate_limit_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """保存可测试策略依赖；构造阶段不触发 Redis 或网络请求。"""
        self._source = source  # 使用稳定来源名隔离 RPS 窗口和冷却状态。
        self._settings = settings  # 复用已通过 Pydantic 校验的全局策略配置。
        self._requests_per_second = requests_per_second  # 保留每个供应商独立的基础频率限制。
        self._source_rate_limiter = source_rate_limiter or get_source_rate_limiter()  # Redis 缺席时该边界会安全回退。
        self._retry_sleep = retry_sleep  # 允许测试独立记录退避等待参数而不真实等待。
        self._rate_limit_sleep = rate_limit_sleep  # 允许测试独立跳过或观测正常 RPS 等待。
        self._random_uniform = random_uniform  # 允许测试固定 jitter，避免不稳定断言。
        self._now = now  # 允许测试固定 HTTP 日期计算基准。
        self._rate_limit_lock = asyncio.Lock()  # 串行化当前实例的来源请求起始时间。
        self._next_request_at = 0.0  # 保存当前进程内下一次允许发送请求的单调时间。
        self._cooldown_until = 0.0  # 保存当前实例中来源冷却的单调时间截止点。

    async def execute(
        self,
        request: Callable[[], Awaitable[httpx.Response]],
        *,
        response_status: Callable[[httpx.Response], int] | None = None,
    ) -> httpx.Response:
        """执行一个幂等请求，并在临时失败时按统一策略重试。

        参数：
            request：只应执行 GET 等幂等 HTTP 请求的异步回调。
            response_status：可选的供应商错误信封状态分类器；默认使用 HTTP 状态码。
        返回：
            httpx.Response：成功响应或已耗尽重试预算的 HTTP 响应，由适配器转换为领域异常。
        异常：
            AcademicApiNetworkError：临时网络错误在预算耗尽后抛出。
            SourceCooldownError：本地或 Redis 冷却期内拒绝继续请求。
        """
        status_for = response_status or (lambda response: response.status_code)  # 默认严格使用 HTTP 状态码。
        max_attempts = self._settings.academic_api_max_retries + 1  # 首次请求加上配置化重试预算。
        for attempt in range(1, max_attempts + 1):  # 每次重试都必须重新经过来源限流。
            await self._acquire_request_window()  # 先检查冷却，再协调 Redis 与本地 RPS。
            try:  # 网络类异常与 HTTP 响应使用不同的最终返回边界。
                response = await request()  # 调用方负责构造 URL、参数和认证头。
            except httpx.RequestError as error:  # 当前学术适配器只发 GET，因此可安全自动重试。
                if attempt >= max_attempts:  # 耗尽预算后不再放大外部故障。
                    logger.error("学术来源网络请求失败：来源=%s，尝试=%d/%d，错误类别=%s", self._source, attempt, max_attempts, type(error).__name__)  # 不记录请求细节。
                    raise AcademicApiNetworkError(f"{self._source} 网络请求失败") from None  # 由适配器映射为既有领域错误。
                await self._wait_before_retry(attempt, max_attempts, None, None, type(error).__name__)  # 网络错误没有 Retry-After，使用指数退避。
                continue  # 等待完成后重新占用来源窗口。
            status_code = status_for(response)  # 支持少数供应商以成功 HTTP 状态返回的公开限流信封。
            if status_code not in RETRYABLE_STATUS_CODES:  # 成功及不可重试 4xx 均立即交回适配器。
                return response  # 保留来源专属 HTTP 错误映射契约。
            retry_after = parse_retry_after(response.headers.get("Retry-After"), now=self._now(), maximum_seconds=self._settings.academic_api_retry_after_max_seconds)  # 安全读取供应商等待建议。
            if attempt >= max_attempts:  # 最后一次响应不再等待。
                if status_code == 429:  # 最终限流必须阻断当前和其他进程的后续访问。
                    await self._activate_cooldown(retry_after)  # 同步写入本地与 Redis 冷却状态。
                logger.error("学术来源请求重试耗尽：来源=%s，状态码=%d，尝试=%d/%d，错误类别=http_status", self._source, status_code, attempt, max_attempts)  # 输出无敏感信息的终态日志。
                return response  # 由适配器保留原有领域异常类型和状态消息。
            await self._wait_before_retry(attempt, max_attempts, status_code, retry_after, "http_status")  # 记录并等待下一次尝试。
        raise AssertionError("学术来源重试循环不应无响应退出")  # 防御性保证静态类型与控制流完整。

    async def _acquire_request_window(self) -> None:
        """在每次发送前执行本地冷却、Redis 冷却、跨进程窗口和本地 RPS。"""
        loop = asyncio.get_running_loop()  # 使用单调时钟避免系统时间回拨影响冷却。
        remaining_seconds = self._cooldown_until - loop.time()  # 读取当前实例剩余冷却时间。
        if remaining_seconds > 0:  # 本地冷却期间不得继续触发 HTTP transport。
            logger.warning("学术来源处于本地冷却期：来源=%s，剩余秒数=%.3f", self._source, remaining_seconds)  # 仅记录安全摘要。
            raise SourceCooldownError(f"{self._source} 当前处于本地冷却期")  # 让适配器快速降级。
        await self._source_rate_limiter.acquire(self._source, self._requests_per_second)  # Redis 不可用时内部自动回退。
        async with self._rate_limit_lock:  # 阻止同一实例并发绕过供应商 RPS。
            now = loop.time()  # 在持锁后读取可靠的单调时间。
            wait_seconds = max(0.0, self._next_request_at - now)  # 计算本地来源窗口的剩余等待。
            if wait_seconds > 0:  # 首次请求无需等待。
                logger.info("学术来源本地限流等待：来源=%s，秒数=%.3f", self._source, wait_seconds)  # 不记录查询或 URL。
                await self._rate_limit_sleep(wait_seconds)  # 正常 RPS 等待不应混入重试退避观测。
            self._next_request_at = loop.time() + (1.0 / self._requests_per_second)  # 从实际发送前预约下一次窗口。

    async def _wait_before_retry(self, attempt: int, max_attempts: int, status_code: int | None, retry_after: float | None, error_category: str) -> None:
        """计算带 jitter 的供应商等待或指数退避，并输出统一可观测日志。"""
        if retry_after is None:  # 缺失或非法响应头时采用默认指数退避。
            base_wait = min(self._settings.academic_api_backoff_max_seconds, self._settings.academic_api_backoff_initial_seconds * (2 ** (attempt - 1)))  # 产生 15、30、60 秒序列。
            wait_source = "exponential_backoff"  # 标记日志中的等待策略来源。
        else:  # 供应商明确建议等待时优先尊重该值。
            base_wait = retry_after  # 已在解析器中截断到安全上限。
            wait_source = "retry_after"  # 标记供应商控制的等待策略。
        jitter = self._random_uniform(0.0, self._settings.academic_api_jitter_max_seconds)  # 每次重试均加入受配置约束的随机抖动。
        wait_seconds = base_wait + jitter  # 避免多个进程同时恢复并再次形成尖峰。
        logger.warning("学术来源临时不可用，等待重试：来源=%s，状态码=%s，当前尝试=%d，最大尝试=%d，等待秒数=%.3f，策略=%s，已添加抖动=%s，是否进入冷却=%s，冷却秒数=%.3f，错误类别=%s", self._source, status_code, attempt, max_attempts, wait_seconds, wait_source, jitter > 0, False, 0.0, error_category)  # 严格避免记录 URL、查询、密钥和响应正文。
        await self._retry_sleep(wait_seconds)  # 让出事件循环后再重新经过所有来源限流边界。

    async def _activate_cooldown(self, retry_after: float | None) -> None:
        """激活不短于三十秒的来源冷却，并尽力同步给 Redis。"""
        base_cooldown = max(30.0, self._settings.academic_api_cooldown_seconds)  # 强制满足来源冷却最小三十秒的产品约束。
        cooldown_seconds = max(base_cooldown, retry_after or 0.0)  # 更长的有效 Retry-After 优先于默认冷却。
        loop = asyncio.get_running_loop()  # 使用与本地 RPS 一致的单调时钟记录截止点。
        self._cooldown_until = max(self._cooldown_until, loop.time() + cooldown_seconds)  # 只延长，绝不缩短已有冷却。
        synchronized = await self._source_rate_limiter.penalize(self._source, cooldown_seconds)  # Redis 不可用时仍保留本地冷却。
        logger.warning("学术来源已进入冷却：来源=%s，状态码=429，当前尝试=%d，最大尝试=%d，等待秒数=0.000，策略=cooldown，已添加抖动=%s，是否进入冷却=%s，冷却秒数=%.3f，错误类别=http_status，Redis已同步=%s", self._source, self._settings.academic_api_max_retries + 1, self._settings.academic_api_max_retries + 1, False, True, cooldown_seconds, synchronized)  # 输出安全且完整的终态观测字段。
