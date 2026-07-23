"""验证后端启动时只回收无法续跑的孤儿搜索运行。"""

from sqlalchemy import create_engine, select  # 创建隔离内存库并读取冗余状态列和结果表。
from sqlalchemy.orm import sessionmaker  # 为仓储和回收服务提供独立短生命周期会话。

from backend.app.models.multi_round_search import MultiRoundSearchResult  # 构造仅 completed 运行已有的结果快照。
from backend.app.models.query_intent import QueryIntent  # 构造搜索运行必须保留的最小查询意图。
from backend.app.models.search_run import SearchRunState  # 构造不同生命周期状态的轻量快照。
from backend.app.repositories.database import Base  # 创建已注册的搜索运行 ORM 表。
from backend.app.repositories.search_runs import INTERRUPTED_SEARCH_ERROR, INTERRUPTED_SEARCH_STOP_REASON, SearchRunRepository, SearchRunResultRow, SearchRunRow  # 验证回收状态、错误去重和结果表边界。
from backend.app.services.search_run_recovery import SearchRunRecoveryService  # 执行不依赖控制器、来源或模型的启动回收。


def _state(status: str, *, errors: list[str] | None = None) -> SearchRunState:
    """构造可写入 SQLite 的最小搜索运行状态。"""
    intent = QueryIntent(original_query="检索中断恢复边界", normalized_query="interrupted search recovery", query_language="mixed")  # 构造不含真实用户数据的测试查询。
    return SearchRunState(query_intent=intent, search_mode="standard", max_rounds=2, current_round=1, status=status, errors=errors or [])  # 保持所有状态共享同一轻量快照契约。


def test_startup_recovery_marks_only_pending_and_running_runs_failed_and_keeps_results_boundary() -> None:
    """启动回收应终态化孤儿运行、保持其他终态不变，且不创建结果快照。"""
    engine = create_engine("sqlite:///:memory:")  # 创建完全隔离的内存 SQLite，避免触碰用户数据。
    Base.metadata.create_all(engine)  # 创建搜索运行和结果 ORM 表。
    session_factory = sessionmaker(bind=engine)  # 为每次仓储操作提供独立短生命周期会话。
    pending = _state("pending")  # 构造尚未开始但上次进程已退出的孤儿运行。
    running = _state("running")  # 构造执行中被进程中断的孤儿运行。
    completed = _state("completed")  # 构造必须保持不变的完成运行。
    failed = _state("failed", errors=[INTERRUPTED_SEARCH_ERROR])  # 构造已失败且已有相同错误的终态运行。
    cancelled = _state("cancelled")  # 构造必须保持不变的取消运行。
    for state in [pending, running, completed, failed, cancelled]:  # 先持久化所有生命周期状态模拟一次服务重启前的数据库。
        session = session_factory()  # 为每次保存创建独立事务会话。
        try:  # 确保即使断言失败也释放 SQLite 会话。
            SearchRunRepository(session).save(state)  # 写入轻量状态快照，不启动控制器或发起网络调用。
        finally:  # 保存后立即释放会话。
            session.close()  # 避免测试中复用已关闭事务。
    session = session_factory()  # 创建仅用于 completed 结果快照的独立会话。
    try:  # 保持完整结果与运行状态分离的现有边界。
        SearchRunRepository(session).save_result(MultiRoundSearchResult(run_state=completed, query_intent=completed.query_intent, papers=[]))  # 仅 completed 运行拥有既有结果行。
    finally:  # 结果写入后释放会话。
        session.close()  # 防止测试连接泄漏。

    recovered_count = SearchRunRecoveryService(session_factory).reconcile_interrupted_search_runs()  # 模拟后端启动时的纯 SQLite 回收步骤。
    second_recovered_count = SearchRunRecoveryService(session_factory).reconcile_interrupted_search_runs()  # 再次执行验证多次启动幂等。

    session = session_factory()  # 读取回收后的冗余列、JSON 快照与结果行。
    try:  # 确保所有断言完成后释放会话和内存引擎。
        repository = SearchRunRepository(session)  # 通过正式仓储读取已校验的领域状态。
        pending_after = repository.get(pending.run_id)  # 恢复被回收的 pending 快照。
        running_after = repository.get(running.run_id)  # 恢复被回收的 running 快照。
        completed_after = repository.get(completed.run_id)  # 恢复不应变更的 completed 快照。
        failed_after = repository.get(failed.run_id)  # 恢复不应重复写入的 failed 快照。
        cancelled_after = repository.get(cancelled.run_id)  # 恢复不应变更的 cancelled 快照。
        rows = {row.run_id: row for row in session.scalars(select(SearchRunRow)).all()}  # 同时读取冗余 status 列验证其与 JSON 一致。
        result_ids = set(session.scalars(select(SearchRunResultRow.run_id)).all())  # 验证回收不创建 pending/running 对应的结果行。

        assert recovered_count == 2 and second_recovered_count == 0  # 验证只首次回收两个非终态孤儿运行且后续启动幂等。
        assert pending_after is not None and pending_after.status == "failed" and running_after is not None and running_after.status == "failed"  # 验证 pending 与 running 都转换为 failed。
        assert pending_after.stop_reason == INTERRUPTED_SEARCH_STOP_REASON and running_after.stop_reason == INTERRUPTED_SEARCH_STOP_REASON  # 验证两条孤儿记录使用统一可展示停止原因。
        assert pending_after.errors.count(INTERRUPTED_SEARCH_ERROR) == 1 and running_after.errors.count(INTERRUPTED_SEARCH_ERROR) == 1  # 验证错误摘要被追加且没有重复项。
        assert rows[pending.run_id].status == pending_after.status and rows[running.run_id].status == running_after.status  # 验证冗余 status 列与 state_json 中的领域状态一致。
        assert completed_after is not None and completed_after.status == "completed"  # 验证 completed 运行不会被启动回收修改。
        assert failed_after is not None and failed_after.status == "failed" and failed_after.errors.count(INTERRUPTED_SEARCH_ERROR) == 1  # 验证 failed 运行不被重复追加中断错误。
        assert cancelled_after is not None and cancelled_after.status == "cancelled"  # 验证 cancelled 运行不会被启动回收修改。
        assert result_ids == {completed.run_id}  # 验证回收不生成 search_run_results，也不删除既有 completed 结果。
        assert repository.delete_terminal_run(running.run_id) == "deleted"  # 验证回收后的 failed 记录继续可由既有终态清理接口删除。
    finally:  # 无论断言结果如何都释放测试资源。
        session.close()  # 关闭最后一个会话。
        engine.dispose()  # 释放内存 SQLite 引擎。
