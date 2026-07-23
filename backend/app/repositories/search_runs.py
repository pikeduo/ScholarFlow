"""使用 SQLite 持久化可恢复的轻量搜索运行状态快照。"""

from datetime import datetime, timezone  # 生成无歧义的运行状态更新时间。
from collections.abc import Sequence  # 标注批量论文标识输入的只读序列。

from sqlalchemy import DateTime, String, Text, select  # 声明运行表字段并按运行标识查询。
from sqlalchemy.orm import Mapped, Session, mapped_column  # 声明 ORM 映射和请求级事务边界。

from backend.app.models.search_run import SearchRunState  # 读写统一且已校验的搜索运行领域状态。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 保存 SSE 完成后可按运行标识读取的最终结果。
from backend.app.models.paper import PaperRecord  # 从已保存最终结果中恢复单篇论文详情。
from backend.app.models.search_run_history import SearchRunHistoryItem  # 返回可展示搜索问题的本地运行索引。
from backend.app.repositories.database import Base  # 注册到统一 SQLite 元数据。


INTERRUPTED_SEARCH_STOP_REASON = "后端进程中断，搜索未完成"  # 为启动回收后的失败运行提供统一可展示原因。
INTERRUPTED_SEARCH_ERROR = "搜索任务因服务进程中断而终止，请重新发起检索"  # 追加到轻量快照的安全错误说明。


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

    def reconcile_interrupted_runs(self) -> int:
        """将单进程上次遗留的 pending/running 运行原子标记为失败。

        返回：
            int：本次由运行中状态实际迁移为 failed 的记录数。
        异常：
            ValueError：任一待回收快照无法通过领域模型校验时抛出，事务不会部分提交。
        """
        rows = self._session.scalars(  # 只扫描冗余状态列明确为非终态的上次运行。
            select(SearchRunRow).where(SearchRunRow.status.in_(("pending", "running")))  # completed、failed、cancelled 永不进入回收集合。
        ).all()
        if not rows:  # 多次启动或没有中断任务时无需写入。
            return 0  # 保持幂等并避免无意义更新时间变化。
        now = datetime.now(timezone.utc)  # 为同一批次迁移使用统一 UTC 更新时间。
        for row in rows:  # 先完成全部状态 JSON 校验，再由事务统一提交。
            state = SearchRunState.model_validate_json(row.state_json)  # 不信任冗余列，确保领域快照可被安全更新。
            errors = list(state.errors)  # 复制历史安全错误，避免就地修改领域对象。
            if INTERRUPTED_SEARCH_ERROR not in errors:  # 防止异常重试或旧数据造成相同错误重复追加。
                errors.append(INTERRUPTED_SEARCH_ERROR)  # 给用户提供明确的重新检索行动提示。
            recovered_state = state.model_copy(  # 同步修改 JSON 状态、停止原因和可展示错误摘要。
                update={"status": "failed", "stop_reason": INTERRUPTED_SEARCH_STOP_REASON, "errors": errors}
            )
            row.status = recovered_state.status  # 同步冗余状态列，保证历史筛选与 JSON 状态一致。
            row.state_json = recovered_state.model_dump_json(exclude_none=False)  # 覆盖为已校验且不含重复错误的状态快照。
            row.updated_at = now  # 让历史列表按实际回收时间排序。
        self._session.commit()  # 单次提交所有状态迁移；不读取、创建或修改 search_run_results。
        return len(rows)  # 返回实际被回收的非终态运行数量。

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

    def list_history(self, limit: int) -> list[SearchRunHistoryItem]:
        """按最近更新时间倒序读取有限运行索引，不返回论文内容。

        参数：
            limit：已由 API 限制在合理范围内的最大返回数量。
        返回：
            list[SearchRunHistoryItem]：可恢复、可清理的本地运行元数据。
        """
        rows = self._session.scalars(  # 仅读取轻量状态表，避免列表页解析完整论文结果 JSON。
            select(SearchRunRow).order_by(SearchRunRow.updated_at.desc()).limit(limit)
        ).all()
        result_ids = set(  # 一次查询取得存在完整结果的运行标识，避免逐条查询导致 N+1。
            self._session.scalars(select(SearchRunResultRow.run_id).where(SearchRunResultRow.run_id.in_([row.run_id for row in rows]))).all()
        ) if rows else set()
        return [  # 仅投影页面和删除边界所需的安全字段。
            _history_item_from_row(row, result_ready=row.run_id in result_ids)  # 重新校验轻量状态后构造稳定索引。
            for row in rows  # 保持 SQLite 按更新时间给出的稳定倒序。
        ]

    def delete_terminal_run(self, run_id: str) -> str:
        """删除一条终态运行的轻量状态和同次完整结果快照。

        参数：
            run_id：用户显式确认后请求清理的稳定运行标识。
        返回：
            str：``deleted``、``missing`` 或 ``active`` 三种受控删除结果。
        """
        state_row = self._session.get(SearchRunRow, run_id)  # 先读取状态以确认运行存在且可安全删除。
        if state_row is None:  # 不存在运行无需执行删除事务。
            return "missing"  # 让 API 映射为稳定 404。
        state = SearchRunState.model_validate_json(state_row.state_json)  # 重新校验状态，避免仅信任冗余状态列。
        if state.status not in {"completed", "failed", "cancelled"}:  # 运行中删除会与后台写入竞争，必须拒绝。
            return "active"  # 让 API 返回明确 409 而非尝试中断工作流。
        result_row = self._session.get(SearchRunResultRow, run_id)  # 同时读取可选完整结果快照。
        if result_row is not None:  # 已完成结果存在时必须与状态一起清理。
            self._session.delete(result_row)  # 先删除依赖同一运行标识的结果行。
        self._session.delete(state_row)  # 删除终态轻量运行状态行。
        self._session.commit()  # 原子提交两张表的清理，避免留下可恢复孤儿数据。
        return "deleted"  # 通知调用方本地快照已彻底清理。

    def get_paper(self, paper_id: str) -> PaperRecord | None:
        """从 SQLite 最终结果快照中读取一篇论文的最新可展示详情。

        参数：
            paper_id：已由后端生成并在搜索结果中展示的稳定论文标识。
        返回：
            PaperRecord | None：最近保存结果中的论文，或尚未保存时的空值。
        """
        rows = self._session.scalars(select(SearchRunResultRow).order_by(SearchRunResultRow.updated_at.desc())).all()  # 先读取较新的快照，使重复论文始终返回最近一次融合结果。
        for row in rows:  # 搜索快照规模受最终结果数控制，避免为详情入口访问外部来源。
            result = MultiRoundSearchResult.model_validate_json(row.result_json)  # 复用最终结果的领域校验恢复论文集合。
            for paper in result.papers:  # 逐篇比对内部稳定标识。
                if paper.paper_id == paper_id:  # 找到时立即返回，保持较新快照优先。
                    return paper  # 返回完整规范化论文记录供详情接口展示。
        return None  # 所有已保存快照都未命中时交由 API 映射为 404。

    def get_papers(self, paper_ids: Sequence[str]) -> list[PaperRecord]:
        """从 SQLite 最终结果快照批量读取论文，并保持请求标识顺序。

        参数：
            paper_ids：已完成去重与数量校验的内部论文标识序列。
        返回：
            list[PaperRecord]：命中的最新论文详情；缺失标识不会出现在结果中。
        """
        requested_ids = set(paper_ids)  # 构造集合以避免对每篇论文重复线性比较。
        found_papers: dict[str, PaperRecord] = {}  # 保存较新快照优先的命中记录。
        rows = self._session.scalars(select(SearchRunResultRow).order_by(SearchRunResultRow.updated_at.desc())).all()  # 先读取更新更晚的最终结果快照。
        for row in rows:  # 快照只保存最终小集合，可安全执行确定性扫描。
            result = MultiRoundSearchResult.model_validate_json(row.result_json)  # 恢复经过领域校验的完整论文集合。
            for paper in result.papers:  # 逐篇检查是否属于本次对比请求。
                if paper.paper_id in requested_ids and paper.paper_id not in found_papers:  # 只保留最近快照中的同一论文。
                    found_papers[paper.paper_id] = paper  # 保存完整规范化记录供对比服务复用。
            if len(found_papers) == len(requested_ids):  # 所有请求论文已命中时无需继续解析较旧快照。
                break  # 缩短详情较多时的无效 SQLite 扫描。
        return [found_papers[paper_id] for paper_id in paper_ids if paper_id in found_papers]  # 按用户选择顺序返回，便于前端固定列展示。


def _lightweight_snapshot(state: SearchRunState) -> SearchRunState:
    """移除大论文集合，保留可恢复控制流、统计、候选 ID 与覆盖报告。"""
    return state.model_copy(update={"normalized_papers": [], "final_papers": []})  # 遵循工作流状态不重复存储完整候选的大小控制规则。


def _history_item_from_row(row: SearchRunRow, *, result_ready: bool) -> SearchRunHistoryItem:
    """将 ORM 轻量状态行投影为带搜索问题和 UTC 时间的历史索引项。"""
    state = SearchRunState.model_validate_json(row.state_json)  # 复用领域模型校验历史 JSON 格式。
    return SearchRunHistoryItem(  # 只返回本地历史展示、恢复和删除边界所需字段。
        run_id=state.run_id,
        query_text=state.query_intent.original_query,
        status=state.status,
        current_round=state.current_round,
        max_rounds=state.max_rounds,
        selected_sources=state.selected_sources,
        stop_reason=state.stop_reason,
        result_ready=result_ready,
        created_at=_as_utc_datetime(row.created_at),
        updated_at=_as_utc_datetime(max(row.updated_at, row.created_at)),
    )


def _as_utc_datetime(value: datetime) -> datetime:
    """将 SQLite 可能丢失偏移的时间恢复为明确 UTC 时刻。

    参数：
        value：ORM 读取的创建或更新时间。
    返回：
        datetime：携带 UTC 偏移、可被前端正确换算本地时间的时间。
    """
    if value.tzinfo is None:  # SQLite 的 DateTime 即使声明 timezone=True 也可能返回朴素 UTC 时间。
        return value.replace(tzinfo=timezone.utc)  # 按写入约定补回 UTC 偏移，避免浏览器误按本地时间解释。
    return value.astimezone(timezone.utc)  # 其他数据库返回带偏移时间时统一规范化为 UTC。
