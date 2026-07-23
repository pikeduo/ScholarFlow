"""验证应用生命周期会在接受请求前执行搜索运行回收。"""

import asyncio  # 在普通 pytest 用例中驱动 FastAPI 异步生命周期上下文。

from fastapi import FastAPI  # 构造不注册业务路由的最小生命周期宿主。

from backend.app import main  # 导入待测启动生命周期与其可替换基础设施依赖。


class _FakeRedisManager:
    """记录生命周期调用顺序的无网络 Redis 替身。"""

    def __init__(self, events: list[str]) -> None:
        """保存顺序断言使用的共享事件列表。"""
        self._events = events  # 让测试观察启动和关闭顺序。

    async def start(self) -> None:
        """记录 Redis 启动，不创建真实连接。"""
        self._events.append("redis_start")  # 标记基础设施降级管理器启动完成。

    async def close(self) -> None:
        """记录 Redis 关闭，不访问外部服务。"""
        self._events.append("redis_close")  # 标记应用退出时释放顺序。


def test_lifespan_recovers_interrupted_runs_after_database_initialization(monkeypatch) -> None:
    """应用启动必须先建表、再回收孤儿运行，且整个步骤不启动控制器或外部调用。"""
    events: list[str] = []  # 收集受控替身的调用顺序。

    class FakeRecoveryService:
        """只记录回收调用的服务替身，不提供控制器、适配器或 LLM 入口。"""

        def reconcile_interrupted_search_runs(self) -> int:
            """模拟仅 SQLite 的启动回收成功。"""
            events.append("recovery")  # 记录回收发生在接收请求前。
            return 0  # 模拟本次没有遗留运行。

    monkeypatch.setattr(main, "initialize_database", lambda: events.append("database"))  # 阻断真实数据库初始化并记录顺序。
    monkeypatch.setattr(main, "SearchRunRecoveryService", FakeRecoveryService)  # 注入不具备任何外部调用能力的回收替身。
    monkeypatch.setattr(main, "get_redis_manager", lambda: _FakeRedisManager(events))  # 注入不连接网络的 Redis 生命周期替身。

    async def exercise_lifespan() -> None:
        """进入并退出一次 FastAPI 生命周期。"""
        async with main.lifespan(FastAPI()):  # 触发启动回收，再触发正常关闭逻辑。
            events.append("serving")  # 标记回收完成后才允许应用处理请求。

    asyncio.run(exercise_lifespan())  # 在不启动 HTTP 服务的情况下执行生命周期。

    assert events == ["database", "recovery", "redis_start", "serving", "redis_close"]  # 验证回收紧随建表、早于请求处理和 Redis 后续生命周期。
