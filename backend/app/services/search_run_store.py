"""提供可由多轮控制器调用的 SQLite 搜索运行状态存储适配层。"""

from collections.abc import Callable  # 标注可替换的数据库会话工厂。
from typing import Protocol  # 定义控制器依赖的最小状态保存协议。

from sqlalchemy.exc import SQLAlchemyError  # 将持久化异常映射为服务边界错误。
from sqlalchemy.orm import Session  # 标注会话工厂返回的请求级会话。

from backend.app.core.logging import logger  # 记录不含完整用户查询的持久化异常。
from backend.app.models.search_run import SearchRunState  # 读写统一的可恢复运行状态。
from backend.app.repositories.database import SessionLocal  # 默认创建独立 SQLite 会话。
from backend.app.repositories.search_runs import SearchRunRepository  # 使用仓储隔离 ORM 和领域状态。


class SearchRunStateStore(Protocol):
    """定义控制器保存和恢复运行状态所需的最小持久化协议。"""

    def save(self, state: SearchRunState) -> None:
        """持久化最新运行状态快照。"""
        ...  # 具体存储可替换为 SQLite、Redis 或测试替身。

    def get(self, run_id: str) -> SearchRunState | None:
        """按运行标识恢复最新状态快照。"""
        ...  # 不存在时由实现返回空值。


class SearchRunStoreError(RuntimeError):
    """表示搜索运行状态无法安全保存或恢复。"""


class SqliteSearchRunStateStore:
    """使用短生命周期 SQLite 会话保存和读取搜索运行状态。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        """保存可由测试替换的 SQLAlchemy 会话工厂。

        参数：
            session_factory：每次存取状态时创建独立事务会话的可调用对象。
        """
        self._session_factory = session_factory  # 让进程级控制器不会错误共享请求事务。

    def save(self, state: SearchRunState) -> None:
        """保存轻量状态快照，持久化失败时抛出安全服务错误。

        参数：
            state：控制器当前最新的可恢复运行状态。
        异常：
            SearchRunStoreError：SQLite 事务无法完成时抛出。
        """
        session = self._session_factory()  # 为当前状态写入创建独立会话。
        try:  # 将 ORM 细节隔离在存储适配层。
            SearchRunRepository(session).save(state)  # 委托仓储完成轻量化、覆盖写入和提交。
        except SQLAlchemyError as exc:  # 不将 SQL、路径或底层异常传给控制器和 API。
            session.rollback()  # 回滚可能未完成的事务避免连接复用污染。
            logger.exception("搜索运行状态保存失败：运行=%s，状态=%s", state.run_id, state.status)  # 只记录运行标识与状态，不记录完整查询。
            raise SearchRunStoreError("搜索运行状态暂时无法保存") from exc  # 返回稳定可处理的存储错误。
        finally:  # 无论成功或失败都释放会话。
            session.close()  # 防止长时多轮检索积累数据库连接。

    def get(self, run_id: str) -> SearchRunState | None:
        """读取最新轻量状态快照，数据库异常时抛出安全服务错误。

        参数：
            run_id：需要恢复或查看的运行标识。
        返回：
            SearchRunState | None：已校验快照或不存在时的空值。
        异常：
            SearchRunStoreError：读取或解析持久化状态失败时抛出。
        """
        session = self._session_factory()  # 为当前读取创建独立短生命周期会话。
        try:  # 仓储负责将 JSON 恢复为领域状态。
            return SearchRunRepository(session).get(run_id)  # 不存在时稳定返回空值。
        except (SQLAlchemyError, ValueError) as exc:  # 覆盖数据库故障和历史快照格式异常。
            logger.exception("搜索运行状态读取失败：运行=%s", run_id)  # 不记录完整查询或状态 JSON。
            raise SearchRunStoreError("搜索运行状态暂时无法读取") from exc  # 返回安全稳定的服务错误。
        finally:  # 所有读取路径均释放会话。
            session.close()  # 防止运行状态轮询占用连接。
