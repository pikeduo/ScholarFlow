"""封装学术来源响应的可降级 Redis JSON 缓存。"""

import hashlib  # 为规范化参数生成不暴露查询正文的稳定摘要。
import json  # 以可跨进程读取的 JSON 形式保存来源响应。
from contextvars import ContextVar, Token  # 为单次异步搜索隔离缓存命中计数。
from dataclasses import dataclass  # 保存轻量且可共享的本次搜索缓存统计。
from collections.abc import Mapping  # 校验缓存键输入中的参数对象。
from typing import Protocol  # 定义缓存依赖的最小 Redis 管理器边界。

from backend.app.core.logging import logger  # 记录不包含查询与响应正文的缓存统计。
from backend.app.repositories.redis_client import RedisAsyncClient, RedisClientManager, get_redis_manager  # 复用已验证 Redis 客户端与降级状态。


@dataclass
class SourceCacheUsage:
    """保存单次搜索生命周期内的来源响应缓存命中数。"""

    hit_count: int = 0  # 仅在读取到结构有效的 Redis 列表时递增。


_source_cache_usage: ContextVar[SourceCacheUsage | None] = ContextVar("source_cache_usage", default=None)  # 隔离并发搜索的统计上下文。


def begin_source_cache_usage() -> Token[SourceCacheUsage | None]:
    """开始记录当前异步搜索的来源缓存命中。"""

    return _source_cache_usage.set(SourceCacheUsage())  # 为当前任务及其并发子任务共享同一统计对象。


def end_source_cache_usage(token: Token[SourceCacheUsage | None]) -> int:
    """结束当前搜索的缓存统计并返回命中次数。"""

    usage = _source_cache_usage.get()  # 在重置前读取当前搜索累计的有效命中数。
    _source_cache_usage.reset(token)  # 避免后续独立请求继承本次搜索上下文。
    return usage.hit_count if usage is not None else 0  # 防御未初始化上下文时仍返回稳定零值。


def record_source_cache_hit() -> None:
    """将一次有效来源缓存命中计入当前搜索，未启用统计时静默忽略。"""

    usage = _source_cache_usage.get()  # 获取当前异步调用链绑定的统计对象。
    if usage is not None:  # 适配器独立调用缓存时不强制要求存在搜索工作流。
        usage.hit_count += 1  # 并发子任务共享该对象，因而可以汇总同轮各来源命中。


class RedisClientProvider(Protocol):
    """定义来源缓存获取 Redis 客户端所需的最小能力。"""

    def get_client(self) -> RedisAsyncClient | None:
        """返回已验证 Redis 客户端，不可用时返回空值。"""
        ...  # 保持缓存层不承担连接与健康探测职责。


class SourceResponseCache:
    """缓存学术来源已规范化请求的原始响应数组，Redis 不可用时自动失效。"""

    def __init__(self, redis_provider: RedisClientProvider, search_ttl_seconds: int) -> None:
        """保存 Redis 获取边界与已校验的搜索结果 TTL。"""
        self._redis_provider = redis_provider  # 不持有裸连接，便于生命周期关闭后安全降级。
        self._search_ttl_seconds = search_ttl_seconds  # 统一限制来源搜索响应的短期保留时间。

    def build_key(self, source: str, operation: str, params: Mapping[str, str | int], adapter_version: str = "v1") -> str:
        """根据来源、操作和规范化参数构造不暴露查询文本的缓存键。

        参数：
            source：稳定来源名称，例如 ``openalex``。
            operation：适配器内稳定操作名称，例如 ``search``。
            params：不含密钥和认证头的来源请求参数。
            adapter_version：响应字段或映射契约变化时递增的版本标识。
        返回：
            str：包含 SHA-256 摘要的 Redis 键，不含原始查询正文。
        """
        normalized_payload = json.dumps(  # 用稳定顺序序列化参数，保证等价请求共享缓存。
            {"adapter_version": adapter_version, "params": dict(sorted(params.items()))},  # 只纳入无敏感认证信息的请求参数。
            ensure_ascii=False,  # 保留 Unicode 语义，但摘要和日志均不输出正文。
            separators=(",", ":"),  # 排除无意义空白，保证摘要在进程间一致。
            sort_keys=True,  # 保证嵌套字典键顺序稳定。
        )
        digest = hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()  # 使用 UTF-8 生成固定长度不可逆键摘要。
        return f"source:cache:{source}:{operation}:{adapter_version}:{digest}"  # 以模块、子模块与复合唯一标识隔离同一 DB 0 中的缓存键。

    async def get_list(self, key: str, source: str, operation: str) -> list[object] | None:
        """读取缓存的 JSON 数组；连接、解码或结构异常均安全回退未命中。"""
        client = self._redis_provider.get_client()  # 仅取得生命周期已经 ping 成功的客户端。
        if client is None:  # Redis 禁用或不可用时不应影响来源检索主路径。
            return None  # 让适配器直接走既有来源请求流程。
        try:  # Redis 命令和 JSON 解码均可能因短期存储故障失败。
            raw_value = await client.get(key)  # 读取可能为 bytes、str 或空值的缓存内容。
            if raw_value is None:  # 键不存在或已过期代表正常缓存未命中。
                return None  # 保持调用方回源语义。
            text_value = raw_value.decode("utf-8") if isinstance(raw_value, bytes) else raw_value  # 兼容 redis-py 二进制和测试替身文本返回。
            decoded_value = json.loads(text_value) if isinstance(text_value, str) else None  # 仅接受有效 JSON 文本。
            if not isinstance(decoded_value, list):  # 防止错误键、损坏数据或旧格式进入适配器映射。
                logger.warning("学术来源缓存结构无效，已忽略：来源=%s，操作=%s", source, operation)  # 不记录缓存键或查询正文。
                return None  # 让当前请求安全回源而不是使用不可信缓存。
        except Exception:  # Redis 网络短暂故障、超时或 JSON 解析错误均不阻断搜索。
            logger.warning("学术来源缓存读取失败，已回源：来源=%s，操作=%s", source, operation, exc_info=True)  # 记录受控堆栈且不泄露键内容。
            return None  # 继续既有网络请求路径。
        logger.info("学术来源缓存命中：来源=%s，操作=%s，结果数=%d", source, operation, len(decoded_value))  # 记录可观测缓存命中统计。
        record_source_cache_hit()  # 仅将结构有效且实际可复用的 Redis 响应计入用量。
        return decoded_value  # 返回经 JSON 校验的来源原始结果数组。

    async def set_list(self, key: str, source: str, operation: str, value: list[object]) -> None:
        """以搜索 TTL 写入 JSON 数组；失败只记录警告且不影响已获得的来源结果。"""
        client = self._redis_provider.get_client()  # 仅使用已验证 Redis 客户端。
        if client is None:  # Redis 不可用时保持无状态回退，不积压本地缓存。
            return  # 当前来源结果仍会正常返回给上层。
        try:  # 缓存写入属于旁路优化，不应改变搜索成功语义。
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))  # 使用 UTF-8 可逆 JSON 保存原始响应数组。
            await client.set(key, payload, ex=self._search_ttl_seconds)  # 依赖 Redis TTL 自动清理短期来源结果。
        except Exception:  # Redis 命令、序列化或连接失败均应被安全降级。
            logger.warning("学术来源缓存写入失败，已忽略：来源=%s，操作=%s", source, operation, exc_info=True)  # 不记录键、查询或响应正文。
            return  # 保持已经成功的来源检索结果可用。
        logger.info("学术来源缓存已写入：来源=%s，操作=%s，结果数=%d", source, operation, len(value))  # 记录不含查询的缓存写入统计。


def get_source_response_cache() -> SourceResponseCache:
    """构造使用应用全局 Redis 生命周期管理器的来源缓存访问器。"""
    manager: RedisClientManager = get_redis_manager()  # 保持所有缓存操作复用同一已验证连接池。
    return SourceResponseCache(manager, manager.source_search_cache_ttl_seconds)  # 从集中配置读取 TTL，避免适配器散落环境变量访问。
