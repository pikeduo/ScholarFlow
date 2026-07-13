"""验证学术来源 Redis 响应缓存的键隔离、TTL 与降级边界。"""

import asyncio  # 在同步 pytest 用例中驱动缓存异步方法。

from backend.app.repositories.source_cache import SourceResponseCache, begin_source_cache_usage, end_source_cache_usage  # 导入待测来源响应缓存与本次搜索命中统计边界。


class FakeRedisClient:
    """提供内存键值与可控失败的 Redis 异步客户端替身。"""

    def __init__(self, should_fail: bool = False) -> None:
        """保存故障开关、内存值和最近一次 TTL。"""
        self._should_fail = should_fail  # 控制读写时是否模拟 Redis 临时故障。
        self.values: dict[str, str] = {}  # 保存测试期内的 JSON 文本缓存值。
        self.last_ttl_seconds: int | None = None  # 记录写入时使用的过期时间。

    async def get(self, key: str) -> str | None:
        """读取内存值，必要时模拟 Redis 读取失败。"""
        if self._should_fail:  # 覆盖 Redis 断连时的可降级读取路径。
            raise ConnectionError("模拟 Redis 读取失败")  # 让缓存层安全回退为未命中。
        return self.values.get(key)  # 返回已写入文本或表示未命中的空值。

    async def set(self, key: str, value: str, ex: int) -> bool:
        """写入内存值并记录 Redis TTL 参数。"""
        if self._should_fail:  # 覆盖 Redis 断连时的旁路写入路径。
            raise ConnectionError("模拟 Redis 写入失败")  # 不允许写入失败影响来源结果。
        self.values[key] = value  # 保存缓存 JSON 文本。
        self.last_ttl_seconds = ex  # 记录调用方显式传入的 TTL。
        return True  # 模拟 redis-py 成功写入响应。


class FakeRedisProvider:
    """提供可选客户端的生命周期管理器替身。"""

    def __init__(self, client: FakeRedisClient | None) -> None:
        """保存当前可用或不可用的 Redis 客户端替身。"""
        self._client = client  # 空值表示 Redis 未启用或健康检查失败。

    def get_client(self) -> FakeRedisClient | None:
        """返回当前可用客户端或空值。"""
        return self._client  # 模拟真实管理器的已验证客户端访问边界。


def test_source_cache_uses_hashed_key_and_search_ttl() -> None:
    """等价来源参数应共享不含查询正文的哈希键，并使用配置 TTL 写入。"""
    client = FakeRedisClient()  # 构造不连接真实 Redis 的内存替身。
    cache = SourceResponseCache(FakeRedisProvider(client), search_ttl_seconds=3600)  # 注入固定一小时 TTL 的缓存实例。
    params = {"query": "Transformer forecasting ETT", "limit": 10}  # 构造包含敏感业务查询正文但不含认证信息的来源参数。
    key = cache.build_key("semantic_scholar", "search", params)  # 构造应对外隐藏查询正文的缓存键。
    assert "Transformer forecasting ETT" not in key  # 验证 Redis 键不直接暴露完整查询文本。
    assert key.startswith("source:cache:semantic_scholar:search:v1:")  # 验证键采用模块、子模块和来源操作隔离边界。
    asyncio.run(cache.set_list(key, "semantic_scholar", "search", [{"paperId": "paper-1"}]))  # 写入可序列化的来源响应数组。
    cached = asyncio.run(cache.get_list(key, "semantic_scholar", "search"))  # 读取同一键验证缓存命中。
    assert cached == [{"paperId": "paper-1"}]  # 验证 JSON 往返不改变来源响应结构。
    assert client.last_ttl_seconds == 3600  # 验证写入必须始终携带受控 TTL。


def test_source_cache_counts_only_valid_hits_in_current_search() -> None:
    """结构有效的缓存读取应只计入当前搜索的命中统计。"""
    client = FakeRedisClient()  # 构造无需真实 Redis 的内存客户端。
    cache = SourceResponseCache(FakeRedisProvider(client), search_ttl_seconds=60)  # 构造待测来源响应缓存。
    key = cache.build_key("openalex", "works", {"query": "forecasting"})  # 使用不暴露查询正文的稳定键。
    asyncio.run(cache.set_list(key, "openalex", "works", [{"id": "work-1"}]))  # 预先写入结构正确的 JSON 列表。

    usage_token = begin_source_cache_usage()  # 开始模拟一次完整多源搜索的统计上下文。
    try:  # 确保断言失败时仍会清理 ContextVar。
        assert asyncio.run(cache.get_list(key, "openalex", "works")) == [{"id": "work-1"}]  # 读取有效缓存以触发命中计数。
    finally:
        cache_hits = end_source_cache_usage(usage_token)  # 结束统计并恢复独立上下文。

    assert cache_hits == 1  # 验证有效缓存命中被准确记录一次。


def test_source_cache_degrades_when_redis_is_unavailable() -> None:
    """Redis 缺席或命令失败时，缓存层必须返回未命中且不抛出来源调用异常。"""
    key = "source:cache:openalex:works:v1:test"  # 使用不含查询信息的模块化固定测试键。
    unavailable_cache = SourceResponseCache(FakeRedisProvider(None), search_ttl_seconds=60)  # 模拟 Redis 未启用或健康检查失败。
    failed_cache = SourceResponseCache(FakeRedisProvider(FakeRedisClient(should_fail=True)), search_ttl_seconds=60)  # 模拟 Redis 已连接后短暂故障。
    assert asyncio.run(unavailable_cache.get_list(key, "openalex", "works")) is None  # 验证无客户端时安全回退未命中。
    assert asyncio.run(failed_cache.get_list(key, "openalex", "works")) is None  # 验证读取失败时安全回退未命中。
    asyncio.run(failed_cache.set_list(key, "openalex", "works", []))  # 验证旁路写入失败不会向来源调用方抛出异常。
