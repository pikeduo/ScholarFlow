"""封装可降级 Redis 客户端生命周期，供缓存、限流与事件层统一复用。"""

from collections.abc import Callable  # 标注可替换 Redis 客户端工厂。
from typing import Protocol  # 定义无需依赖真实 Redis 的最小异步客户端协议。

from backend.app.core.config import Settings, settings  # 读取集中配置而不让业务层解析环境变量。
from backend.app.core.logging import logger  # 记录连接降级与关闭异常的安全日志。


class RedisAsyncClient(Protocol):
    """定义本项目 Redis 生命周期所需的最小异步客户端接口。"""

    async def ping(self) -> object:
        """验证当前客户端是否可与 Redis 服务端通信。"""
        ...  # 真实实现由 redis.asyncio.Redis 提供，测试可注入替身。

    async def aclose(self) -> object:
        """释放客户端连接池和关联网络资源。"""
        ...  # 真实实现由 redis.asyncio.Redis 提供，测试可注入替身。

    async def get(self, key: str) -> bytes | str | None:
        """读取短期存储中的二进制或文本值。"""
        ...  # 缓存层只依赖最小键值读取能力。

    async def set(self, key: str, value: str, ex: int) -> object:
        """以过期时间写入短期存储值。"""
        ...  # 缓存层显式要求 TTL，避免形成无限期数据。


RedisClientFactory = Callable[[], RedisAsyncClient]  # 允许测试替换客户端构造而不连接真实 Redis。


class RedisClientManager:
    """管理可选 Redis 客户端的启动、健康状态和关闭，不承担具体缓存业务。

    Redis 仅用于可丢失的短期数据；启动失败会保留 ``unavailable`` 状态并让应用继续使用
    SQLite 与进程内回退逻辑。后续缓存、限流和 SSE 模块只能通过本管理器取得已验证客户端。
    """

    def __init__(self, config: Settings, client_factory: RedisClientFactory | None = None) -> None:
        """保存配置和可替换客户端工厂，不在构造阶段导入或连接 Redis。"""
        self._config = config  # 保存生命周期内不变的启用开关、地址、前缀和超时配置。
        self._client_factory = client_factory  # 保存可选替身工厂，生产环境延迟创建真实工厂。
        self._client: RedisAsyncClient | None = None  # 仅在 ping 成功后保存可供业务层使用的客户端。
        self._status = "disabled" if not config.redis_enabled else "unavailable"  # 初始化为安全回退状态，禁止假定 Redis 已连接。

    @property
    def status(self) -> str:
        """返回 ``disabled``、``available`` 或 ``unavailable`` 的当前短期存储状态。"""
        return self._status  # 供健康检查和日志读取，不暴露 Redis 地址或认证信息。

    @property
    def key_prefix(self) -> str:
        """返回已校验键前缀，供后续缓存和限流适配器构造命名空间。"""
        return self._config.redis_key_prefix  # 统一避免业务模块自行拼接不一致命名空间。

    @property
    def source_search_cache_ttl_seconds(self) -> int:
        """返回学术来源搜索响应的 Redis TTL 秒数。"""
        return self._config.redis_source_search_cache_ttl_seconds  # 集中提供缓存配置，避免泄露内部配置对象。

    def get_client(self) -> RedisAsyncClient | None:
        """返回已验证客户端；Redis 禁用或不可用时返回空值以触发调用方降级。"""
        return self._client if self._status == "available" else None  # 禁止业务层使用尚未 ping 成功的连接。

    async def start(self) -> bool:
        """按配置连接并 ping Redis，失败时记录警告且不阻止应用启动。"""
        if not self._config.redis_enabled:  # 开发或单机环境可显式保持无 Redis 运行。
            self._status = "disabled"  # 保持健康检查可解释的禁用状态。
            logger.info("Redis 短期存储未启用，使用 SQLite 与进程内降级路径")  # 不输出连接地址或潜在认证信息。
            return False  # 调用方无需等待或重试。
        client: RedisAsyncClient | None = None  # 在导入或构造失败时保留可安全判断的空客户端。
        try:  # 将连接与 ping 失败统一降级为非致命状态。
            client = self._create_client()  # 仅在启用时按需导入并创建异步客户端。
            await client.ping()  # 验证客户端既已构造又能实际访问 Redis。
        except Exception:  # Redis 驱动导入、认证、网络和超时均不得阻止 SQLite 服务启动。
            if client is not None:  # 客户端已部分构造时才需要释放连接池资源。
                await _close_quietly(client)  # 释放可能部分建立的连接池资源。
            self._client = None  # 明确禁止后续业务使用失败客户端。
            self._status = "unavailable"  # 健康端点和调用方可据此展示降级。
            logger.warning("Redis 短期存储不可用，已降级为 SQLite 与进程内路径", exc_info=True)  # 记录受控堆栈但不输出 URL 或认证信息。
            return False  # 让生命周期继续启动 FastAPI。
        self._client = client  # 只有 ping 成功才发布客户端给后续缓存、限流或事件模块。
        self._status = "available"  # 标记短期存储已通过可用性校验。
        logger.info("Redis 短期存储已连接，键前缀=%s", self.key_prefix)  # 仅记录非敏感命名空间。
        return True  # 通知生命周期或测试 Redis 已成功接入。

    async def close(self) -> None:
        """关闭已验证客户端；关闭失败不影响 FastAPI 其余资源释放。"""
        client = self._client  # 保存局部引用避免关闭期间状态被重复读取。
        self._client = None  # 先撤销客户端发布，防止关闭过程中仍被新请求使用。
        if client is not None:  # 仅在此前连接成功时释放真实连接池。
            await _close_quietly(client)  # 将关闭异常降级为日志而不遮蔽应用停止流程。
        if self._config.redis_enabled:  # 启用环境关闭后应表达为暂时不可用而不是配置禁用。
            self._status = "unavailable"  # 保持健康状态与生命周期真实状态一致。

    def _create_client(self) -> RedisAsyncClient:
        """延迟创建 Redis 客户端，隔离第三方导入和连接配置细节。"""
        if self._client_factory is not None:  # 单元测试可避免导入真实 redis 包或访问网络。
            return self._client_factory()  # 返回测试提供的最小协议替身。
        try:  # 生产环境仅在 Redis 实际启用时导入依赖。
            from redis.asyncio import from_url  # 延迟加载 redis-py 异步客户端避免模块导入副作用。
        except ImportError as error:  # 依赖遗漏应进入可解释降级路径。
            raise RuntimeError("Redis 运行时依赖不可用") from error  # 不暴露 Python 环境绝对路径。
        return from_url(  # 创建连接池客户端但不在此处发送网络请求。
            self._config.redis_url,
            decode_responses=False,
            socket_connect_timeout=self._config.redis_socket_timeout_seconds,
            socket_timeout=self._config.redis_socket_timeout_seconds,
        )  # Redis 类型满足最小异步客户端协议。


async def _close_quietly(client: RedisAsyncClient) -> None:
    """安全关闭部分建立或已验证的 Redis 客户端，避免关闭异常阻塞应用生命周期。"""
    try:  # 部分连接失败后也应尽力释放连接池。
        await client.aclose()  # 调用 redis-py 异步客户端标准关闭方法。
    except Exception:  # 关闭阶段错误不应掩盖原始连接或应用停止原因。
        logger.warning("Redis 客户端关闭异常，已忽略", exc_info=True)  # 不输出连接地址或认证信息。


redis_manager = RedisClientManager(settings)  # 创建全进程共享管理器但不在模块导入阶段连接 Redis。


def get_redis_manager() -> RedisClientManager:
    """返回应用生命周期统一启动和关闭的 Redis 管理器。"""
    return redis_manager  # 保持缓存、限流、事件和健康检查使用同一连接池状态。
