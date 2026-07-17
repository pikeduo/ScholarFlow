"""在单进程服务启动时回收被中断的搜索运行。"""

from collections.abc import Callable  # 标注可由测试替换的 SQLite 会话工厂。

from sqlalchemy.exc import SQLAlchemyError  # 将数据库异常隔离为受控启动失败。
from sqlalchemy.orm import Session  # 标注短生命周期事务会话。

from backend.app.core.logging import logger  # 记录不含查询正文的回收统计和异常。
from backend.app.repositories.database import SessionLocal  # 使用应用默认 SQLite 会话工厂。
from backend.app.repositories.search_runs import SearchRunRepository  # 委托仓储执行原子状态迁移。


class SearchRunRecoveryError(RuntimeError):
    """表示启动回收无法安全完成，应用不应带着不一致运行状态继续启动。"""


class SearchRunRecoveryService:
    """将单进程上次遗留的 pending/running 搜索运行统一标记为失败。

    不尝试恢复 asyncio 任务、调用控制器或外部依赖；只修改 SQLite 的轻量状态快照。
    """

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        """保存可替换的短生命周期 SQLite 会话工厂。

        参数：
            session_factory：每次启动回收创建独立事务会话的工厂。
        """
        self._session_factory = session_factory  # 保持服务不持有跨请求或跨启动的数据库会话。

    def reconcile_interrupted_search_runs(self) -> int:
        """原子回收上一个单进程实例遗留的运行中搜索记录。

        返回：
            int：本次实际从 pending/running 转为 failed 的运行数量。
        异常：
            SearchRunRecoveryError：SQLite 写入或历史状态解析失败时抛出。
        """
        session = self._session_factory()  # 为整个批次创建单一原子事务边界。
        try:  # 仓储只读取并更新运行快照表，不访问结果表或外部依赖。
            recovered_count = SearchRunRepository(session).reconcile_interrupted_runs()  # 统一提交所有孤儿运行的终态迁移。
        except (SQLAlchemyError, ValueError) as exc:  # 覆盖事务故障及不合法历史 JSON。
            session.rollback()  # 防止部分回收结果被保留。
            logger.exception("搜索运行启动回收失败")  # 记录完整堆栈但不记录用户查询或状态 JSON。
            raise SearchRunRecoveryError("搜索运行启动回收失败") from exc  # 让生命周期停止，避免服务继续制造更多孤儿状态。
        finally:  # 成功、失败都必须释放短生命周期会话。
            session.close()  # 防止启动异常或重载时泄漏 SQLite 连接。
        if recovered_count:  # 仅在确有遗留运行时输出可观测统计。
            logger.warning("已回收因后端进程中断而终止的搜索运行：数量=%s", recovered_count)  # 不记录查询正文或论文内容。
        return recovered_count  # 让启动入口与离线测试可验证实际迁移数量。
