"""验证多轮搜索控制器的查询推进、跨轮去重与保护性停止条件。"""

import asyncio  # 在同步 pytest 用例中执行控制器协程。

from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 构造单轮协调器固定结果。
from backend.app.models.paper import PaperRecord  # 构造已核验的最终论文候选。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 构造主查询与待执行补充子查询。
from backend.app.models.source_routing import SourceRoutePlan  # 构造可审计的来源计划。
from backend.app.agents.search_workflow import MultiRoundSearchWorkflow  # 验证实际 LangGraph 节点编排边界。
from backend.app.services.multi_round_search import MultiRoundSearchController  # 导入待测多轮搜索控制器。


class _StubCoordinator:
    """按调用顺序返回固定单轮结果并记录实际执行的轮次查询。"""

    def __init__(self, results: list[MultiSourceRecallResult]) -> None:
        """保存不访问网络、模型或文件系统的预设单轮结果。"""
        self._results = list(results)  # 复制结果列表避免测试调用期间外部修改。
        self.queries: list[QueryIntent] = []  # 记录控制器实际交给协调器的每轮查询。

    async def recall(self, query: QueryIntent) -> MultiSourceRecallResult:
        """返回下一条固定结果并拒绝超出测试预期的调用。"""
        self.queries.append(query)  # 保存当前轮查询供断言验证。
        if not self._results:  # 额外调用意味着控制器没有正确停止。
            raise AssertionError("控制器执行了未预期的额外轮次")  # 让测试立即暴露无限或重复检索风险。
        return self._results.pop(0)  # 返回已完成排序与核验的离线结果。


class _RecordingStateStore:
    """记录控制器每轮保存状态的离线存储替身。"""

    def __init__(self) -> None:
        """初始化按保存顺序保留的状态快照列表。"""
        self.saved_states = []  # 保存控制器交给持久化边界的运行状态。

    def save(self, state: object) -> None:
        """记录状态对象而不访问 SQLite、Redis 或文件系统。"""
        self.saved_states.append(state)  # 保持调用顺序供轮次边界断言。

    def get(self, _: str) -> None:
        """满足状态存储协议的读取方法，本用例不需要恢复读取。"""
        return None  # 控制器只在本轮执行中调用保存操作。


class _DirectExecutor:
    """记录 LangGraph 执行节点委托参数的离线多轮服务替身。"""

    def __init__(self, result: object) -> None:
        """保存工作流应原样返回的稳定多轮结果。"""
        self._result = result  # 保存预设完成结果。
        self.calls: list[tuple[QueryIntent, bool, object | None]] = []  # 记录工作流传递给直接服务的参数。

    async def run_direct(self, query: QueryIntent, *, budget_exhausted: bool = False, event_publisher: object | None = None) -> object:
        """记录调用且不访问真实来源、模型或存储。"""
        self.calls.append((query, budget_exhausted, event_publisher))  # 保存节点委托边界供断言。
        return self._result  # 返回预设的完整结果。


def _query(*, target_paper_count: int = 2, search_mode: str = "standard", subqueries: list[QuerySubquery] | None = None) -> QueryIntent:
    """构造可按用例控制目标数量、模式和待执行子查询的查询意图。"""
    return QueryIntent(  # 提供无需 Query Agent 或外部 API 的稳定领域输入。
        original_query="检索时间序列预测论文",  # 保存完整用户查询以满足核心契约。
        normalized_query="time series forecasting",  # 提供首轮主查询文本。
        query_language="mixed",  # 标记中英文混合输入。
        research_topics=["time series forecasting"],  # 提供来源适配器首轮使用的结构化主题。
        target_paper_count=target_paper_count,  # 允许用例构造易验证的小目标数量。
        source_recall_count=5,  # 保持来源召回数量不小于最终目标。
        search_mode=search_mode,  # 控制标准或深度模式最大轮次。
        subqueries=subqueries or [],  # 注入后续轮次应执行的补充查询。
    )


def _paper(paper_id: str, *, doi: str | None = None) -> PaperRecord:
    """构造具备可信核验状态的高相关论文候选。"""
    return PaperRecord(paper_id=paper_id, title=f"Forecasting Paper {paper_id}", abstract="Time series forecasting result.", doi=doi, source="openalex", constraint_status="satisfied", llm_relevance_score=0.9)  # 让覆盖分析将其计入高相关结果。


def _round_result(papers: list[PaperRecord], *, source_errors: dict[str, str] | None = None) -> MultiSourceRecallResult:
    """构造单来源离线召回、排序和核验完成后的最小结果。"""
    return MultiSourceRecallResult(  # 返回控制器实际需要的路由、论文、来源数量和错误字段。
        route_plan=SourceRoutePlan(academic_sources=["openalex"], selection_reasons={"openalex": "测试来源"}),  # 声明本轮使用一个学术来源。
        papers=papers,  # 注入本轮已排序和核验的论文。
        source_counts={"openalex": len(papers)},  # 以论文数量模拟来源成功返回数量。
        source_errors=source_errors or {},  # 注入可选的安全来源错误。
        raw_paper_count=len(papers),  # 提供最小原始召回统计。
        work_family_count=len(papers),  # 提供不影响控制器判断的版本族统计。
    )


def test_controller_executes_planned_subquery_and_stops_after_target_is_covered() -> None:
    """首轮不足时控制器应执行计划子查询，第二轮达到目标后停止。"""
    subquery = QuerySubquery(query="ETT time series forecasting", language="en", purpose="dataset")  # 构造首轮后应优先执行的 Query Agent 子查询。
    coordinator = _StubCoordinator([_round_result([_paper("paper-1")]), _round_result([_paper("paper-2")])])  # 依次返回一篇和两篇累计目标所需的论文。

    result = asyncio.run(MultiRoundSearchController(coordinator).run(_query(subqueries=[subquery])))  # 执行不访问网络的标准模式两轮控制。

    assert len(coordinator.queries) == 2  # 验证目标不足时只额外执行一轮补充查询。
    assert coordinator.queries[0].normalized_query == "time series forecasting"  # 验证首轮保留主查询。
    assert coordinator.queries[1].research_topics == ["ETT time series forecasting"]  # 验证第二轮实际切换为计划子查询。
    assert [paper.paper_id for paper in result.papers] == ["paper-1", "paper-2"]  # 验证跨轮候选按首次出现顺序累积。
    assert result.run_state.status == "completed"  # 验证控制器写入可恢复完成状态。
    assert result.run_state.stop_reason == "已获得目标数量的高相关论文且关键约束已覆盖"  # 验证达到目标后不继续搜索。


def test_controller_stops_when_no_executable_query_exists() -> None:
    """首轮不足但没有计划或演化出的新查询时不得重复调用主查询。"""
    coordinator = _StubCoordinator([_round_result([_paper("paper-1")])])  # 仅提供首轮结果以检测错误的重复调用。

    result = asyncio.run(MultiRoundSearchController(coordinator).run(_query()))  # 执行没有补充子查询的标准模式搜索。

    assert len(coordinator.queries) == 1  # 验证没有新查询时首轮后立即停止。
    assert result.run_state.stop_reason == "没有可执行的新查询"  # 验证停止原因不错误归因为来源或预算。


def test_controller_stops_on_second_deep_round_without_new_identity() -> None:
    """深度模式第二轮只返回同 DOI 论文时，应按边际收益不足停止而不是凑数。"""
    subquery = QuerySubquery(query="ETT time series forecasting", language="en", purpose="dataset")  # 提供允许进入第二轮的计划子查询。
    coordinator = _StubCoordinator([_round_result([_paper("paper-1", doi="10.1000/example")]), _round_result([_paper("paper-1-updated", doi="10.1000/example")])])  # 第二轮返回相同 DOI 但不同内部 ID 的重复论文。

    result = asyncio.run(MultiRoundSearchController(coordinator).run(_query(search_mode="deep", subqueries=[subquery])))  # 执行允许三轮的深度模式搜索。

    assert len(coordinator.queries) == 2  # 验证重复论文后不会继续执行第三轮。
    assert [paper.paper_id for paper in result.papers] == ["paper-1-updated"]  # 验证同 DOI 记录更新而不虚增累计数量。
    assert result.run_state.stop_reason == "连续轮次新增高质量论文不足"  # 验证第二轮无新增触发边际收益停止。


def test_controller_stops_when_all_selected_sources_are_unavailable() -> None:
    """所有已选来源本轮失败时应保留空结果并返回来源不足停止原因。"""
    coordinator = _StubCoordinator([_round_result([], source_errors={"openalex": "学术来源调用失败"})])  # 构造唯一学术来源失败的单轮结果。

    result = asyncio.run(MultiRoundSearchController(coordinator).run(_query()))  # 执行不访问网络的单来源失败场景。

    assert len(coordinator.queries) == 1  # 验证来源全失败时不会继续重复调用。
    assert result.papers == []  # 验证控制器不虚构论文或使用低相关候选填充。
    assert result.run_state.stop_reason == "可用学术来源不足"  # 验证返回安全且可展示的来源不足原因。


def test_controller_persists_initial_round_and_completed_snapshots() -> None:
    """装配状态存储后，控制器应在首轮前、轮次后和终态保存可恢复快照。"""
    state_store = _RecordingStateStore()  # 构造不访问真实数据库的持久化记录替身。
    coordinator = _StubCoordinator([_round_result([_paper("paper-1")])])  # 提供首轮后因无新查询停止的固定结果。

    result = asyncio.run(MultiRoundSearchController(coordinator, state_store=state_store).run(_query()))  # 执行带状态存储的标准模式搜索。

    assert len(state_store.saved_states) >= 3  # 验证至少保存初始、首轮和完成状态。
    assert state_store.saved_states[0].status == "running" and state_store.saved_states[0].current_round == 0  # 验证外部来源调用前已持久化初始状态。
    assert state_store.saved_states[-1].status == "completed"  # 验证最终停止原因对应的完成状态已持久化。
    assert state_store.saved_states[-1].stop_reason == result.run_state.stop_reason  # 验证持久化终态与 API 返回保持一致。


def test_langgraph_workflow_executes_direct_service_once_and_returns_its_result() -> None:
    """LangGraph 应经过初始化、执行和结果整理节点，且只委托一次实际服务。"""
    expected = asyncio.run(MultiRoundSearchController(_StubCoordinator([_round_result([_paper("paper-1")])])).run_direct(_query()))  # 先构造不经过图的稳定结果供工作流替身返回。
    executor = _DirectExecutor(expected)  # 注入只记录调用的服务替身。
    workflow = MultiRoundSearchWorkflow(executor)  # 编译包含三个实际节点的 LangGraph 工作流。

    result = asyncio.run(workflow.run(_query(), budget_exhausted=True))  # 运行图并传递预算边界。

    assert result is expected  # 验证工作流返回执行节点产生的同一稳定结果。
    assert len(executor.calls) == 1  # 验证图不会重复调用多源检索服务。
    assert executor.calls[0][1] is True  # 验证预算状态被完整透传至实际服务。
