"""SQLite、Redis 与 FAISS 等存储访问层包。"""

from backend.app.repositories.redis_client import RedisClientManager, get_redis_manager  # 暴露可降级 Redis 生命周期入口。

__all__ = ["RedisClientManager", "get_redis_manager"]  # 限制存储包的显式公共 Redis 接口。
