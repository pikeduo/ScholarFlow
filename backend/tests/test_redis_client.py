"""验证可降级 Redis 生命周期管理器不依赖真实网络或 Redis 服务。"""

import asyncio  # 在同步 pytest 用例中驱动异步 Redis 生命周期方法。

from backend.app.core.config import Settings  # 构造隔离的 Redis 启用与禁用配置。
from backend.app.repositories.redis_client import RedisClientManager, normalize_redis_loopback_url  # 导入待测短期存储生命周期管理器和本地地址规范化函数。


class FakeRedisClient:
    """提供可控 ping 与关闭行为的异步 Redis 客户端替身。"""

    def __init__(self, should_fail: bool = False) -> None:
        """保存是否在 ping 时模拟 Redis 不可用。"""
        self._should_fail = should_fail  # 保存受控连接失败开关。
        self.closed = False  # 记录管理器是否在降级或关闭时释放客户端。

    async def ping(self) -> bool:
        """按测试配置返回连通结果或抛出连接错误。"""
        if self._should_fail:  # 仅在降级测试中模拟网络或认证故障。
            raise ConnectionError("模拟 Redis 连接失败")  # 让管理器进入 unavailable 而不阻止应用。
        return True  # 返回成功连通结果。

    async def aclose(self) -> None:
        """记录连接池释放动作。"""
        self.closed = True  # 让测试验证失败与正常关闭均会释放资源。


def _unexpected_client_factory() -> FakeRedisClient:
    """禁用 Redis 时阻止任何客户端构造。"""
    raise AssertionError("禁用状态不应构造 Redis 客户端")  # 防止测试误把禁用路径变为连接路径。


def test_normalize_redis_loopback_url_prefers_ipv4_without_losing_connection_parts() -> None:
    """本地 localhost 地址应改用 IPv4，且认证、端口、数据库和查询参数必须保留。"""
    original_url = "redis://cache-user:example-password@localhost:6380/2?socket_keepalive=true"  # 使用虚构认证信息覆盖完整连接地址结构。

    normalized_url = normalize_redis_loopback_url(original_url)  # 执行不依赖网络的地址规范化。

    assert normalized_url == "redis://cache-user:example-password@127.0.0.1:6380/2?socket_keepalive=true"  # 验证只替换主机名而不丢失其他连接组成部分。


def test_normalize_redis_loopback_url_keeps_non_localhost_deployments_unchanged() -> None:
    """远程 Redis 与显式 IPv6 Redis 不得被本地兼容逻辑改写。"""
    assert normalize_redis_loopback_url("redis://redis.internal:6379/0") == "redis://redis.internal:6379/0"  # 验证远程部署地址保持不变。
    assert normalize_redis_loopback_url("redis://[::1]:6379/0") == "redis://[::1]:6379/0"  # 验证用户显式指定 IPv6 时保持不变。


def test_redis_manager_keeps_disabled_mode_without_constructing_client() -> None:
    """Redis 未启用时管理器不得构造客户端或尝试网络连接。"""
    manager = RedisClientManager(Settings(redis_enabled=False), client_factory=_unexpected_client_factory)  # 注入会立即失败的工厂验证其不会被调用。

    connected = asyncio.run(manager.start())  # 启动禁用配置的管理器。

    assert connected is False  # 验证禁用状态不会误报连接成功。
    assert manager.status == "disabled"  # 验证健康检查可区分配置禁用。
    assert manager.get_client() is None  # 验证业务层没有可误用的客户端。


def test_redis_manager_connects_and_closes_verified_client() -> None:
    """Redis 可用时管理器应发布已 ping 的客户端，并在关闭时释放它。"""
    client = FakeRedisClient()  # 构造无需真实 Redis 的可用客户端替身。
    manager = RedisClientManager(Settings(redis_enabled=True), client_factory=lambda: client)  # 注入固定客户端工厂。

    connected = asyncio.run(manager.start())  # 执行 ping 验证并发布客户端。

    assert connected is True  # 验证可用 Redis 进入成功状态。
    assert manager.status == "available"  # 验证健康检查会报告可用。
    assert manager.key_prefix == "ScholarFlow"  # 验证默认项目键前缀可供缓存和限流模块统一复用。
    assert manager.get_client() is client  # 验证后续缓存模块只取得已验证客户端。
    asyncio.run(manager.close())  # 模拟 FastAPI 关闭生命周期。
    assert client.closed is True  # 验证连接池在关闭时被释放。
    assert manager.status == "unavailable"  # 验证已启用实例关闭后不再误报可用。


def test_redis_manager_degrades_when_ping_fails() -> None:
    """Redis ping 失败时管理器应关闭客户端并保持应用可继续使用 SQLite。"""
    client = FakeRedisClient(should_fail=True)  # 构造在 ping 时失败的客户端替身。
    manager = RedisClientManager(Settings(redis_enabled=True), client_factory=lambda: client)  # 注入失败客户端工厂。

    connected = asyncio.run(manager.start())  # 执行会失败的 Redis 连通检查。

    assert connected is False  # 验证连接失败不被误报为成功。
    assert manager.status == "unavailable"  # 验证健康状态明确表示已降级。
    assert manager.get_client() is None  # 验证业务层不会使用失败连接。
    assert client.closed is True  # 验证失败后仍释放可能已建立的连接池。
