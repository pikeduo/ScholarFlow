"""验证 Redis 跨进程来源限流和冷却同步不依赖真实 Redis。"""

import asyncio  # 在同步 pytest 用例中驱动异步限流方法。

import pytest  # 断言来源冷却异常。

from backend.app.repositories.source_rate_limiter import SourceCooldownError, SourceRateLimiter  # 导入待测限流器与稳定冷却异常。


class FakeRedisClient:
    """提供支持 NX、TTL 与内存键值的 Redis 客户端替身。"""

    def __init__(self, should_fail: bool = False) -> None:
        """保存故障开关、键值和 TTL 记录。"""
        self._should_fail = should_fail  # 控制 Redis 命令是否模拟短暂故障。
        self.values: dict[str, str] = {}  # 保存来源窗口和冷却键值。
        self.ttls: dict[str, int] = {}  # 保存每个键的剩余 TTL。

    async def get(self, key: str) -> str | None:
        """读取内存值，必要时模拟连接失败。"""
        if self._should_fail:  # 覆盖 Redis 不可用时的本地回退路径。
            raise ConnectionError("模拟 Redis 读取失败")  # 限流器应捕获并回退。
        return self.values.get(key)  # 返回来源窗口或冷却标记。

    async def set(self, key: str, value: str, ex: int, nx: bool | None = None) -> bool:
        """写入内存值，并在 NX 时模拟 Redis 原子占用语义。"""
        if self._should_fail:  # 覆盖 Redis 不可用时的本地回退路径。
            raise ConnectionError("模拟 Redis 写入失败")  # 限流器应捕获并回退。
        if nx and key in self.values:  # 其他进程已经占用同一来源窗口时拒绝当前写入。
            return False  # 模拟 Redis SET NX 未获得锁。
        self.values[key] = value  # 保存来源请求窗口或冷却标记。
        self.ttls[key] = ex  # 记录显式 TTL 供测试验证。
        return True  # 模拟 Redis 成功写入。

    async def ttl(self, key: str) -> int:
        """返回记录的键 TTL，缺失键模拟 Redis 的不存在状态。"""
        return self.ttls.get(key, -2)  # 使用 Redis 标准的键不存在状态码。


class FakeRedisProvider:
    """提供固定命名空间和可选客户端的 Redis 管理器替身。"""

    def __init__(self, client: FakeRedisClient | None) -> None:
        """保存可用或不可用的客户端替身。"""
        self._client = client  # 空值表示 Redis 禁用或健康检查未通过。

    @property
    def key_prefix(self) -> str:
        """返回测试专用键前缀。"""
        return "scholarflow-test"  # 隔离测试键空间。

    def get_client(self) -> FakeRedisClient | None:
        """返回当前已验证客户端或空值。"""
        return self._client  # 模拟真实管理器的降级访问边界。


def test_rate_limiter_acquires_source_window_with_ttl() -> None:
    """首次请求应通过 Redis NX 占用来源窗口，并按 1 RPS 写入一秒 TTL。"""
    client = FakeRedisClient()  # 构造不连接真实 Redis 的内存客户端。
    limiter = SourceRateLimiter(FakeRedisProvider(client))  # 注入测试专用 Redis 生命周期边界。
    acquired = asyncio.run(limiter.acquire("semantic_scholar", requests_per_second=1.0))  # 占用 Semantic Scholar 单请求窗口。
    key = "scholarflow-test:rate:semantic_scholar:request"  # 构造预期的来源级窗口键。
    assert acquired is True  # 验证 Redis 可用时由跨进程限流器完成协调。
    assert client.values[key] == "1"  # 验证窗口键不包含用户查询或认证信息。
    assert client.ttls[key] == 1  # 验证 1 RPS 使用一秒 Redis TTL。


def test_rate_limiter_blocks_remote_cooldown() -> None:
    """其他进程写入来源冷却后，本进程必须在 HTTP 调用前快速失败。"""
    client = FakeRedisClient()  # 构造不连接真实 Redis 的内存客户端。
    cooldown_key = "scholarflow-test:rate:semantic_scholar:cooldown"  # 构造预期的来源级冷却键。
    client.values[cooldown_key] = "1"  # 模拟其他进程收到 429 后写入冷却标记。
    client.ttls[cooldown_key] = 90  # 模拟剩余九十秒冷却时间。
    limiter = SourceRateLimiter(FakeRedisProvider(client))  # 注入包含远程冷却状态的客户端替身。
    with pytest.raises(SourceCooldownError, match="跨进程冷却期"):  # 验证不应继续争抢来源请求窗口。
        asyncio.run(limiter.acquire("semantic_scholar", requests_per_second=1.0))  # 尝试访问已冷却来源。


def test_rate_limiter_penalize_and_redis_failure_fallback() -> None:
    """429 冷却应写入共享键，而 Redis 缺席或失败时必须回退进程内策略。"""
    client = FakeRedisClient()  # 构造可用内存客户端验证冷却同步。
    limiter = SourceRateLimiter(FakeRedisProvider(client))  # 注入可用 Redis 管理器替身。
    synchronized = asyncio.run(limiter.penalize("semantic_scholar", cooldown_seconds=90.0))  # 写入来源级 429 冷却。
    cooldown_key = "scholarflow-test:rate:semantic_scholar:cooldown"  # 构造预期共享冷却键。
    assert synchronized is True  # 验证 Redis 可用时冷却会同步给其他进程。
    assert client.ttls[cooldown_key] == 90  # 验证尊重供应商建议的冷却时长。
    assert asyncio.run(SourceRateLimiter(FakeRedisProvider(None)).acquire("semantic_scholar", 1.0)) is False  # 验证 Redis 禁用时交由进程内限流。
    assert asyncio.run(SourceRateLimiter(FakeRedisProvider(FakeRedisClient(should_fail=True))).acquire("semantic_scholar", 1.0)) is False  # 验证 Redis 命令失败时安全降级。
