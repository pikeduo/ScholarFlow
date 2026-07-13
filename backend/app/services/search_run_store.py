"""提供可由多轮控制器调用的 SQLite 搜索运行状态存储适配层。"""

from collections.abc import Callable  # 标注可替换的数据库会话工厂。
from collections.abc import Sequence  # 标注批量论文标识的只读输入序列。
from typing import Protocol  # 定义控制器依赖的最小状态保存协议。

from sqlalchemy.exc import SQLAlchemyError  # 将持久化异常映射为服务边界错误。
from sqlalchemy.orm import Session  # 标注会话工厂返回的请求级会话。

from backend.app.core.logging import logger  # 记录不含完整用户查询的持久化异常。
from backend.app.models.search_run import SearchRunState  # 读写统一的可恢复运行状态。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 保存 SSE 完成后可读取的完整最终结果。
from backend.app.models.paper import PaperRecord  # 为论文详情读取提供统一领域模型。
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

    def save_result(self, result: MultiRoundSearchResult) -> None:
        """保存一次已完成多轮搜索的完整最终结果。"""
        ...  # 结果存储与轻量运行状态分离。

    def get_result(self, run_id: str) -> MultiRoundSearchResult | None:
        """按运行标识读取已完成多轮搜索的完整最终结果。"""
        ...  # 不存在或尚未完成时返回空值。

    def get_paper(self, paper_id: str) -> PaperRecord | None:
        """按论文标识读取已保存搜索结果中的最新论文详情。"""
        ...  # 不存在时返回空值，详情入口不得调用外部学术来源。

    def get_papers(self, paper_ids: Sequence[str]) -> list[PaperRecord]:
        """按论文标识批量读取已保存搜索结果，供小集合对比使用。"""
        ...  # 不存在标识不在返回结果中，调用方负责统一映射错误。


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

    def save_result(self, result: MultiRoundSearchResult) -> None:
        """保存已完成多轮搜索的完整结果，失败时抛出安全存储错误。"""
        session = self._session_factory()  # 为完成结果写入创建独立短生命周期会话。
        try:  # 隔离 ORM 写入细节和事务边界。
            SearchRunRepository(session).save_result(result)  # 委托仓储写入独立结果快照。
        except SQLAlchemyError as exc:  # 不泄露 SQL、路径或内部响应数据。
            session.rollback()  # 回滚可能未完成的结果写入事务。
            logger.exception("搜索最终结果保存失败：运行=%s", result.run_state.run_id)  # 仅记录运行标识供运维定位。
            raise SearchRunStoreError("搜索最终结果暂时无法保存") from exc  # 返回稳定可处理的存储错误。
        finally:  # 成功或失败均释放会话。
            session.close()  # 防止 SSE 完成后泄漏数据库连接。

    def get_result(self, run_id: str) -> MultiRoundSearchResult | None:
        """读取已完成多轮搜索的完整结果，尚未完成或不存在时返回空值。"""
        session = self._session_factory()  # 为当前结果读取创建独立会话。
        try:  # 仓储负责 JSON 解析和领域模型重新校验。
            return SearchRunRepository(session).get_result(run_id)  # 不存在时稳定返回空值。
        except (SQLAlchemyError, ValueError) as exc:  # 覆盖数据库故障和历史 JSON 格式错误。
            logger.exception("搜索最终结果读取失败：运行=%s", run_id)  # 不记录完整查询或论文内容。
            raise SearchRunStoreError("搜索最终结果暂时无法读取") from exc  # 返回安全错误边界。
        finally:  # 所有读取路径都释放会话。
            session.close()  # 防止前端结果读取长期占用连接。

    def get_paper(self, paper_id: str) -> PaperRecord | None:
        """读取已持久化搜索结果中的论文详情，数据库异常时抛出安全服务错误。

        参数：
            paper_id：需要展示详情的内部论文标识。
        返回：
            PaperRecord | None：最近保存的论文详情，或不存在时的空值。
        异常：
            SearchRunStoreError：SQLite 或历史快照解析异常时抛出。
        """
        session = self._session_factory()  # 为本次详情读取创建独立短生命周期会话。
        try:  # 仅委托仓储扫描 SQLite 最终结果快照。
            return SearchRunRepository(session).get_paper(paper_id)  # 禁止在详情读取路径发起外部学术 API 调用。
        except (SQLAlchemyError, ValueError) as exc:  # 覆盖数据库与历史 JSON 快照格式异常。
            logger.exception("搜索论文详情读取失败：论文=%s", paper_id)  # 仅记录内部标识与完整堆栈，不记录摘要正文。
            raise SearchRunStoreError("论文详情暂时无法读取") from exc  # 向 API 暴露稳定可处理的错误边界。
        finally:  # 成功或失败都必须释放数据库会话。
            session.close()  # 防止详情查看积累空闲连接。

    def get_papers(self, paper_ids: Sequence[str]) -> list[PaperRecord]:
        """批量读取已保存论文详情，数据库异常时抛出安全服务错误。

        参数：
            paper_ids：已经过 API 数量和重复校验的内部论文标识。
        返回：
            list[PaperRecord]：按请求顺序排列的已保存论文记录。
        异常：
            SearchRunStoreError：SQLite 或历史快照解析异常时抛出。
        """
        session = self._session_factory()  # 为本次小集合比较创建独立短生命周期会话。
        try:  # 仓储负责扫描 SQLite 结果快照，不访问任何外部 API。
            return SearchRunRepository(session).get_papers(paper_ids)  # 保持比较与详情读取同一持久化事实边界。
        except (SQLAlchemyError, ValueError) as exc:  # 覆盖数据库与历史 JSON 快照格式异常。
            logger.exception("搜索论文批量读取失败：数量=%s", len(paper_ids))  # 仅记录数量和堆栈，不记录论文内容。
            raise SearchRunStoreError("论文比较数据暂时无法读取") from exc  # 返回稳定公共服务错误。
        finally:  # 成功或失败都释放会话。
            session.close()  # 防止比较请求泄漏数据库连接。
