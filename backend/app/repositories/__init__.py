"""SQLite、Redis 与 FAISS 等存储访问层包。"""

from backend.app.repositories.redis_client import RedisClientManager, get_redis_manager  # 暴露可降级 Redis 生命周期入口。
from backend.app.repositories.source_cache import SourceResponseCache, get_source_response_cache  # 暴露来源适配器可复用的短期响应缓存边界。

__all__ = ["RedisClientManager", "SourceResponseCache", "get_redis_manager", "get_source_response_cache"]  # 限制存储包的显式公共 Redis 接口。
