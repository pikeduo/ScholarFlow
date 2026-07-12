"""使用 SQLite 持久化可恢复的轻量搜索运行状态快照。"""

from datetime import datetime, timezone  # 生成无歧义的运行状态更新时间。

from sqlalchemy import DateTime, String, Text, select  # 声明运行表字段并按运行标识查询。
from sqlalchemy.orm import Mapped, Session, mapped_column  # 声明 ORM 映射和请求级事务边界。

from backend.app.models.search_run import SearchRunState  # 读写统一且已校验的搜索运行领域状态。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 保存 SSE 完成后可按运行标识读取的最终结果。
from backend.app.repositories.database import Base  # 注册到统一 SQLite 元数据。


class SearchRunRow(Base):
    """映射 SQLite 中单次搜索运行的最新轻量状态快照。"""

    __tablename__ = "search_runs"  # 使用稳定表名支持后续 SSE 和恢复功能复用。

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)  # 保存跨 REST、SSE 与持久化关联的 UUID 标识。
    status: Mapped[str] = mapped_column(String(16), index=True)  # 保存 pending、running、completed 等可筛选状态。
    state_json: Mapped[str] = mapped_column(Text)  # 保存不含完整论文集合的 SearchRunState JSON 快照。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 保存首次创建时间便于审计与清理。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 保存最近一次节点或轮次更新时刻。


class SearchRunResultRow(Base):
    """映射 SQLite 中与运行状态分离的最终多轮搜索结果快照。"""

    __tablename__ = "search_run_results"  # 使用独立表避免运行中状态重复保存完整论文集合。

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)  # 与 SearchRunState 使用同一稳定运行标识。
    result_json: Mapped[str] = mapped_column(Text)  # 仅在控制器完成时保存完整公共结果供前端读取。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 记录最终结果写入或重试更新时刻。


class SearchRunRepository:
    """封装搜索运行状态的覆盖写入与按 run_id 恢复读取。"""

    def __init__(self, session: Session) -> None:
        """保存单次操作专用 SQLAlchemy 会话。

        参数：
            session：由调用方管理生命周期的数据库会话。
        """
        self._session = session  # 避免跨请求共享事务或连接。

    def save(self, state: SearchRunState) -> SearchRunState:
        """原子写入最新轻量快照，并保留运行首次创建时间。

        参数：
            state：本轮或节点结束后可恢复的完整领域状态。
        返回：
            SearchRunState：实际写入的轻量快照。
        """
        snapshot = _lightweight_snapshot(state)  # 不重复持久化完整论文列表，仅保存候选引用和状态统计。
        row = self._session.get(SearchRunRow, snapshot.run_id)  # 查询当前运行是否已存在旧快照。
        now = datetime.now(timezone.utc)  # 为本次写入生成统一 UTC 时间。
        if row is None:  # 首次写入时创建运行记录。
            row = SearchRunRow(run_id=snapshot.run_id, status=snapshot.status, state_json=snapshot.model_dump_json(exclude_none=False), created_at=now, updated_at=now)  # 构造完整初始 ORM 行。
            self._session.add(row)  # 加入当前事务等待提交。
        else:  # 后续节点或轮次仅覆盖最新状态。
            row.status = snapshot.status  # 同步常用状态列便于后续按状态筛选。
            row.state_json = snapshot.model_dump_json(exclude_none=False)  # 覆盖为最新且已验证的轻量快照。
            row.updated_at = now  # 记录进度最新更新时间。
        self._session.commit()  # 原子提交新建或覆盖状态。
        return snapshot  # 返回与持久化文本一致的领域对象供调用方审计。

    def get(self, run_id: str) -> SearchRunState | None:
        """按运行标识恢复最新轻量状态，不存在时返回空值。

        参数：
            run_id：调用方提供的 UUID 文本运行标识。
        返回：
            SearchRunState | None：已校验快照或不存在标识的空值。
        """
        row = self._session.scalar(select(SearchRunRow).where(SearchRunRow.run_id == run_id))  # 通过主键语义读取单个最新运行。
        return SearchRunState.model_validate_json(row.state_json) if row is not None else None  # 统一从 JSON 恢复并重新校验领域状态。

    def save_result(self, result: MultiRoundSearchResult) -> None:
        """保存同次运行的完整最终结果，仅供 SSE 完成后的前端读取。

        参数：
            result：多轮控制器已经完成、可安全展示的最终结果。
        """
        row = self._session.get(SearchRunResultRow, result.run_state.run_id)  # 查询同一运行是否已有旧完成结果。
        now = datetime.now(timezone.utc)  # 为本次完成结果写入生成统一 UTC 时间。
        if row is None:  # 首次完成时创建独立结果行。
            row = SearchRunResultRow(run_id=result.run_state.run_id, result_json=result.model_dump_json(exclude_none=False), updated_at=now)  # 构造完整公共结果快照。
            self._session.add(row)  # 加入当前事务等待提交。
        else:  # 重试或恢复完成时覆盖为最新最终结果。
            row.result_json = result.model_dump_json(exclude_none=False)  # 保持读取端只看到最新完成结果。
            row.updated_at = now  # 记录最近结果更新时刻。
        self._session.commit()  # 原子提交完整最终结果快照。

    def get_result(self, run_id: str) -> MultiRoundSearchResult | None:
        """按运行标识读取已完成的完整结果快照，不存在时返回空值。"""
        row = self._session.scalar(select(SearchRunResultRow).where(SearchRunResultRow.run_id == run_id))  # 读取独立结果表避免解析轻量状态。
        return MultiRoundSearchResult.model_validate_json(row.result_json) if row is not None else None  # 恢复完整公开结果供搜索页展示。


def _lightweight_snapshot(state: SearchRunState) -> SearchRunState:
    """移除大论文集合，保留可恢复控制流、统计、候选 ID 与覆盖报告。"""
    return state.model_copy(update={"normalized_papers": [], "final_papers": []})  # 遵循工作流状态不重复存储完整候选的大小控制规则。
