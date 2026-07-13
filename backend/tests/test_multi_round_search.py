"""验证多轮搜索控制器的查询推进、跨轮去重与保护性停止条件。"""

import asyncio  # 在同步 pytest 用例中执行控制器协程。

from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 构造单轮协调器固定结果。
from backend.app.models.paper import PaperRecord  # 构造已核验的最终论文候选。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 构造主查询与待执行补充子查询。
from backend.app.models.source_routing import SourceRoutePlan  # 构造可审计的来源计划。
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


class _FailingCoordinator:
    """模拟单轮召回服务出现未预期内部故障。"""

    def __init__(self) -> None:
        """初始化调用计数，验证失败节点不会触发重试循环。"""
        self.call_count = 0  # 保存召回节点实际执行次数。

    async def recall(self, _: QueryIntent) -> MultiSourceRecallResult:
        """记录调用并抛出不应泄露给 API 的内部异常。"""
        self.call_count += 1  # 记录工作流进入失败分支前的唯一来源调用。
        raise RuntimeError("模拟来源协调器内部故障")  # 触发 LangGraph 召回节点的安全失败路径。


def _query(*, target_paper_count: int = 2, source_recall_count: int = 5, search_mode: str = "standard", subqueries: list[QuerySubquery] | None = None) -> QueryIntent:
    """构造可按用例控制目标数量、模式和待执行子查询的查询意图。"""
    return QueryIntent(  # 提供无需 Query Agent 或外部 API 的稳定领域输入。
        original_query="检索时间序列预测论文",  # 保存完整用户查询以满足核心契约。
        normalized_query="time series forecasting",  # 提供首轮主查询文本。
        query_language="mixed",  # 标记中英文混合输入。
        research_topics=["time series forecasting"],  # 提供来源适配器首轮使用的结构化主题。
        target_paper_count=target_paper_count,  # 允许用例构造易验证的小目标数量。
        source_recall_count=source_recall_count,  # 允许用例分别控制常规来源召回规模和第三轮缺口规模。
        search_mode=search_mode,  # 控制标准或深度模式最大轮次。
        subqueries=subqueries or [],  # 注入后续轮次应执行的补充查询。
    )


def _paper(paper_id: str, *, doi: str | None = None) -> PaperRecord:
    """构造具备可信核验状态的高相关论文候选。"""
    return PaperRecord(paper_id=paper_id, title=f"Forecasting Paper {paper_id}", abstract="Time series forecasting result.", doi=doi, source="openalex", constraint_status="satisfied", llm_relevance_score=0.9)  # 让覆盖分析将其计入高相关结果。


def _round_result(papers: list[PaperRecord], *, source_errors: dict[str, str] | None = None, cache_hit_count: int = 0) -> MultiSourceRecallResult:
    """构造单来源离线召回、排序和核验完成后的最小结果。"""
    return MultiSourceRecallResult(  # 返回控制器实际需要的路由、论文、来源数量和错误字段。
        route_plan=SourceRoutePlan(academic_sources=["openalex"], selection_reasons={"openalex": "测试来源"}),  # 声明本轮使用一个学术来源。
        papers=papers,  # 注入本轮已排序和核验的论文。
        source_counts={"openalex": len(papers)},  # 以论文数量模拟来源成功返回数量。
        source_errors=source_errors or {},  # 注入可选的安全来源错误。
        cache_hit_count=cache_hit_count,  # 注入本轮无需访问外部来源的有效缓存命中数。
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


def test_controller_uses_a_third_gap_recovery_round_after_two_insufficient_rounds() -> None:
    """两轮后仍不足目标时应执行第三轮补足，即使第二轮只返回重复论文。"""
    subquery = QuerySubquery(query="ETT time series forecasting", language="en", purpose="dataset")  # 提供允许进入第二轮的计划子查询。
    coordinator = _StubCoordinator([_round_result([_paper("paper-1", doi="10.1000/example")]), _round_result([_paper("paper-1-updated", doi="10.1000/example")]), _round_result([])])  # 第二轮返回相同 DOI，第三轮模拟补足源未找到新增论文。

    result = asyncio.run(MultiRoundSearchController(coordinator).run(_query(search_mode="deep", subqueries=[subquery])))  # 执行允许第三轮补足的搜索。

    assert len(coordinator.queries) == 3  # 验证两轮不足时仍会进入第三轮补足。
    assert coordinator.queries[2].retrieval_round == 3 and coordinator.queries[2].source_recall_count == 1  # 验证第三轮按剩余一篇的缺口缩小单源返回上限。
    assert [paper.paper_id for paper in result.papers] == ["paper-1-updated"]  # 验证同 DOI 记录更新而不虚增累计数量。
    assert result.run_state.stop_reason == "已达到最大搜索轮次"  # 验证第三轮结束后保留不足结果并受硬轮次上限保护。


def test_controller_sets_the_third_round_source_limit_to_the_remaining_target_gap() -> None:
    """前两轮累计不足二十篇时，第三轮应只请求剩余高相关篇数。"""
    first_subquery = QuerySubquery(query="ETT time series forecasting", language="en", purpose="dataset")  # 提供第二轮可执行的补充查询。
    second_subquery = QuerySubquery(query="Transformer ETT forecasting", language="en", purpose="method")  # 提供第三轮可执行的补充查询。
    first_round_papers = [_paper(f"paper-{index}") for index in range(1, 9)]  # 首轮提供八篇高相关论文。
    second_round_papers = [_paper(f"paper-{index}") for index in range(9, 15)]  # 第二轮再提供六篇高相关论文。
    third_round_papers = [_paper(f"paper-{index}") for index in range(15, 21)]  # 第三轮刚好提供剩余六篇高相关论文。
    coordinator = _StubCoordinator([_round_result(first_round_papers), _round_result(second_round_papers), _round_result(third_round_papers)])  # 按轮次提供无需网络的固定结果。

    result = asyncio.run(MultiRoundSearchController(coordinator).run(_query(target_paper_count=20, source_recall_count=50, subqueries=[first_subquery, second_subquery])))  # 执行以二十篇为目标的三轮检索。

    assert len(coordinator.queries) == 3  # 验证目标不足时执行第三轮而非在第二轮提前停止。
    assert coordinator.queries[0].source_recall_count == 50 and coordinator.queries[1].source_recall_count == 50  # 验证前两轮沿用查询规划的宽召回上限。
    assert coordinator.queries[2].retrieval_round == 3 and coordinator.queries[2].source_recall_count == 6  # 验证第三轮按二十减十四的剩余缺口请求六篇。
    assert len(result.papers) == 20 and result.run_state.stop_reason == "已获得目标数量的高相关论文且关键约束已覆盖"  # 验证补足结果达到目标后正常停止。


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


def test_controller_records_total_latency_and_does_not_count_cached_source_as_api_call() -> None:
    """缓存命中应独立计数，并从本轮实际外部 API 次数中排除。"""
    state_store = _RecordingStateStore()  # 构造记录最终快照的离线存储替身。
    coordinator = _StubCoordinator([_round_result([_paper("paper-1")], cache_hit_count=1)])  # 模拟唯一来源响应由 Redis 有效缓存直接提供。

    result = asyncio.run(MultiRoundSearchController(coordinator, state_store=state_store).run(_query()))  # 执行不访问网络的单轮工作流。

    assert result.run_state.api_call_count == 0  # 验证缓存命中不会被误报为一次外部 API 调用。
    assert result.run_state.cache_hits == 1  # 验证运行快照保留有效缓存命中数。
    assert result.run_state.latency_ms >= 0  # 验证最终结果总是包含非负端到端耗时。
    assert state_store.saved_states[-1].latency_ms == result.run_state.latency_ms  # 验证 SQLite 持久化边界保存的是最终总耗时。


def test_langgraph_conditional_nodes_do_not_recall_when_budget_is_exhausted() -> None:
    """覆盖评估节点发现预算已耗尽时，条件边必须直接进入结果整理。"""
    coordinator = _StubCoordinator([_round_result([_paper("paper-1")])])  # 仅提供首轮结果以检测图不会进入第二轮。

    result = asyncio.run(MultiRoundSearchController(coordinator).run(_query(subqueries=[QuerySubquery(query="ETT time series forecasting", language="en", purpose="dataset")]), budget_exhausted=True))  # 让条件图在首轮覆盖评估后触发预算停止。

    assert len(coordinator.queries) == 1  # 验证预算条件边没有进入查询演化后的第二轮召回。
    assert result.run_state.stop_reason == "搜索预算已达到上限"  # 验证最终结果保留覆盖服务定义的稳定且不泄露成本细节的停止原因。


def test_langgraph_recall_failure_returns_safe_failed_result_without_retry() -> None:
    """召回节点异常时条件图应整理失败结果，而不是重试或泄露内部异常。"""
    coordinator = _FailingCoordinator()  # 注入不访问网络的失败服务替身。

    result = asyncio.run(MultiRoundSearchController(coordinator).run(_query()))  # 执行会进入召回失败分支的条件图。

    assert coordinator.call_count == 1  # 验证失败后不会回到召回节点形成无限循环。
    assert result.run_state.status == "failed"  # 验证状态可供状态接口和前端恢复逻辑消费。
    assert result.run_state.stop_reason == "搜索执行出现内部错误"  # 验证对外只返回稳定安全摘要。
    assert result.papers == []  # 验证未获得论文时不会伪造结果。
