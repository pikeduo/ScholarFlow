"""验证搜索运行 SQLite 仓储的轻量快照、覆盖写入与恢复读取。"""

from sqlalchemy import create_engine  # 创建不访问本地文件的内存 SQLite 测试引擎。
from sqlalchemy.orm import sessionmaker  # 为每个用例创建独立短生命周期会话。

from backend.app.models.paper import PaperRecord  # 构造不应被完整持久化的候选论文。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 构造与 SSE 完成后持久化一致的最终结果。
from backend.app.models.query_intent import QueryIntent  # 构造运行状态必须保留的检索意图。
from backend.app.models.search_run import SearchRunState  # 构造可恢复的搜索运行状态。
from backend.app.repositories.database import Base  # 创建已注册的搜索运行 ORM 表。
from backend.app.repositories.search_runs import SearchRunRepository  # 导入待测状态仓储。


def _state(status: str = "running") -> SearchRunState:
    """构造包含大候选列表的测试状态，以验证仓储会保存轻量快照。"""
    query = QueryIntent(original_query="检索 Transformer 预测", normalized_query="Transformer forecasting", query_language="mixed")  # 构造最小可执行查询意图。
    paper = PaperRecord(paper_id="paper-1", title="Transformer Forecasting", source="openalex")  # 构造一篇完整论文记录。
    return SearchRunState(query_intent=query, search_mode="standard", max_rounds=2, current_round=1, normalized_papers=[paper], candidate_ids=[paper.paper_id], final_papers=[paper], status=status)  # 构造包含候选和最终结果的运行状态。


def test_repository_persists_lightweight_snapshot_and_overwrites_latest_state() -> None:
    """仓储应剥离大论文集合、保留候选 ID，并按 run_id 覆盖最新状态。"""
    engine = create_engine("sqlite:///:memory:")  # 创建隔离的内存数据库避免影响用户数据。
    Base.metadata.create_all(engine)  # 创建已注册的 ORM 表。
    session = sessionmaker(bind=engine)()  # 创建单个测试事务会话。
    try:  # 确保用例结束释放数据库资源。
        repository = SearchRunRepository(session)  # 装配待测仓储。
        initial_state = _state()  # 构造包含完整论文列表的运行状态。
        saved_snapshot = repository.save(initial_state)  # 写入应被轻量化的首轮快照。
        recovered = repository.get(initial_state.run_id)  # 按运行标识恢复持久化状态。
        assert saved_snapshot.normalized_papers == [] and saved_snapshot.final_papers == []  # 验证仓储不会重复存储完整论文集合。
        assert recovered is not None and recovered.candidate_ids == ["paper-1"]  # 验证恢复状态仍保留控制流所需候选引用。
        assert recovered.normalized_papers == [] and recovered.final_papers == []  # 验证 JSON 恢复后仍是轻量快照。
        completed_state = initial_state.model_copy(update={"status": "completed", "stop_reason": "已获得目标数量的高相关论文且关键约束已覆盖"})  # 构造同一运行的完成终态。
        repository.save(completed_state)  # 以相同 run_id 覆盖写入最新状态。
        latest = repository.get(initial_state.run_id)  # 再次恢复应得到终态而非旧快照。
        assert latest is not None and latest.status == "completed"  # 验证状态列和 JSON 快照均已更新。
        assert latest.stop_reason == "已获得目标数量的高相关论文且关键约束已覆盖"  # 验证停止原因可供恢复和 SSE 补偿读取。
        completed_result = MultiRoundSearchResult(run_state=completed_state, query_intent=completed_state.query_intent, papers=completed_state.final_papers)  # 构造应与轻量状态分离保存的完整终态结果。
        repository.save_result(completed_result)  # 写入 SSE 完成后供前端 REST 读取的结果快照。
        recovered_result = repository.get_result(initial_state.run_id)  # 按同一运行标识恢复完整结果。
        assert recovered_result is not None and recovered_result.papers[0].paper_id == "paper-1"  # 验证完整论文只从独立结果表读取。
    finally:  # 无论断言是否失败都关闭会话和引擎。
        session.close()  # 释放内存数据库会话。
        engine.dispose()  # 释放测试引擎资源。
