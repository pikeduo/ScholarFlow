"""封装可降级的 Redis 跨进程来源限流与冷却同步。"""

import asyncio  # 在其他进程持有来源窗口时异步等待，不阻塞事件循环。
import math  # 将来源请求间隔安全转换为 Redis 整秒 TTL。
from typing import Protocol  # 定义限流器所需的最小 Redis 管理器边界。

from backend.app.core.logging import logger  # 记录不含查询与认证信息的限流统计。
from backend.app.repositories.redis_client import RedisAsyncClient, RedisClientManager, get_redis_manager  # 复用已验证 Redis 生命周期管理器。


class SourceCooldownError(RuntimeError):
    """表示其他进程已经为来源写入尚未结束的限流冷却。"""


class RedisRateLimitProvider(Protocol):
    """定义跨进程限流获取客户端和键前缀所需的最小能力。"""

    @property
    def key_prefix(self) -> str:
        """返回已校验 Redis 键前缀。"""
        ...  # 允许单元测试注入内存替身。

    def get_client(self) -> RedisAsyncClient | None:
        """返回已通过健康检查的 Redis 客户端或空值。"""
        ...  # Redis 不可用时必须保持进程内回退路径。


class SourceRateLimiter:
    """使用 Redis NX+TTL 协调来源请求窗口，并同步 429 冷却状态。"""

    def __init__(self, redis_provider: RedisRateLimitProvider) -> None:
        """保存 Redis 生命周期边界，不在构造阶段发送命令或建立连接。"""
        self._redis_provider = redis_provider  # 仅在每次操作时读取当前已验证客户端。

    async def acquire(self, source: str, requests_per_second: float) -> bool:
        """占用来源请求窗口；Redis 不可用时返回假值以启用本地限流。

        参数：
            source：稳定来源名称。
            requests_per_second：来源允许的最大每秒请求次数。
        返回：
            bool：Redis 已参与协调时为真，未启用或失败回退时为假。
        异常：
            SourceCooldownError：任意进程已写入来源冷却状态时抛出。
        """
        client = self._redis_provider.get_client()  # 仅使用生命周期确认可用的 Redis 客户端。
        if client is None:  # 单机或 Redis 故障环境继续使用既有进程内限流。
            return False  # 调用方不应把 Redis 缺席当作来源不可用。
        cooldown_key = self._cooldown_key(source)  # 使用来源级冷却键共享 429 状态。
        request_key = self._request_key(source)  # 使用来源级时间窗口键协调跨进程请求。
        ttl_seconds = max(1, math.ceil(1.0 / requests_per_second))  # Redis 整秒 TTL 至少覆盖来源最小请求间隔。
        try:  # Redis 短暂失败不能阻断既有本地检索流程。
            if await client.get(cooldown_key) is not None:  # 任意进程写入冷却时禁止继续访问供应商。
                remaining_seconds = await client.ttl(cooldown_key)  # 读取剩余时间仅用于安全日志。
                logger.warning("来源处于 Redis 冷却期：来源=%s，剩余秒数=%d", source, max(remaining_seconds, 0))  # 不记录查询内容或键。
                raise SourceCooldownError(f"{source} 当前处于跨进程冷却期")  # 让适配器走其他来源降级。
            while True:  # 直到当前进程原子占用窗口或 Redis 失败回退。
                acquired = await client.set(request_key, "1", ex=ttl_seconds, nx=True)  # 使用 NX 防止并发进程同时占用同一来源窗口。
                if acquired:  # redis-py 成功时返回真值。
                    return True  # 当前进程可以在本地限流检查后发起来源请求。
                remaining_seconds = await client.ttl(request_key)  # 读取其他进程窗口的剩余时间。
                wait_seconds = float(remaining_seconds) if remaining_seconds > 0 else 1.0 / requests_per_second  # 键刚过期或状态异常时退回最小来源间隔。
                logger.info("来源 Redis 限流等待：来源=%s，秒数=%.3f", source, wait_seconds)  # 记录不含查询的跨进程等待统计。
                await asyncio.sleep(wait_seconds)  # 异步等待后重新原子抢占窗口。
                if await client.get(cooldown_key) is not None:  # 等待期间其他进程可能刚收到 429。
                    raise SourceCooldownError(f"{source} 当前处于跨进程冷却期")  # 避免已冷却来源被继续调用。
        except SourceCooldownError:  # 冷却是有效业务状态，应直接交给适配器处理。
            raise  # 保留稳定的来源降级语义。
        except Exception:  # 连接、超时或 Redis 命令错误只触发本地回退。
            logger.warning("来源 Redis 限流不可用，已回退进程内限流：来源=%s", source, exc_info=True)  # 不记录键、查询或认证信息。
            return False  # 调用方继续执行已有本地限流。

    async def penalize(self, source: str, cooldown_seconds: float) -> bool:
        """写入来源冷却状态，使其他进程也能在 429 后立即降级。"""
        client = self._redis_provider.get_client()  # 仅使用已验证 Redis 客户端。
        if client is None:  # Redis 缺席时调用方仍保留自己的进程内冷却。
            return False  # 不将冷却同步失败误判为来源错误。
        ttl_seconds = max(1, math.ceil(cooldown_seconds))  # 以整秒 TTL 表达不短于供应商建议的冷却时间。
        try:  # 冷却同步属于旁路可靠性增强，不应遮蔽原始 429。
            await client.set(self._cooldown_key(source), "1", ex=ttl_seconds)  # 覆盖写入以延长但不缩短本次来源冷却。
        except Exception:  # Redis 短暂故障时继续使用本地冷却。
            logger.warning("来源 Redis 冷却同步失败，已保留进程内冷却：来源=%s", source, exc_info=True)  # 不记录键、查询或认证信息。
            return False  # 向调用方说明跨进程同步未生效。
        logger.warning("来源 Redis 冷却已同步：来源=%s，秒数=%d", source, ttl_seconds)  # 记录安全且可观测的冷却统计。
        return True  # 通知调用方其他进程也会遵守当前冷却。

    def _request_key(self, source: str) -> str:
        """构造来源级跨进程请求窗口键。"""
        return f"{self._redis_provider.key_prefix}:rate:{source}:request"  # 按来源隔离限流计数，不纳入用户查询。

    def _cooldown_key(self, source: str) -> str:
        """构造来源级跨进程 429 冷却键。"""
        return f"{self._redis_provider.key_prefix}:rate:{source}:cooldown"  # 按来源隔离冷却状态，不纳入用户查询。


def get_source_rate_limiter() -> SourceRateLimiter:
    """构造复用应用全局 Redis 生命周期管理器的来源限流器。"""
    manager: RedisClientManager = get_redis_manager()  # 复用缓存与事件层共同的已验证连接池。
    return SourceRateLimiter(manager)  # Redis 缺席时限流器会自动回退，调用方无需分支处理。
