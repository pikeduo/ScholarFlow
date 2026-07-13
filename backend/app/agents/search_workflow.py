"""使用 LangGraph 编排多轮召回、覆盖评估和查询演化。"""

from time import perf_counter  # 使用单调高精度时钟统计一次搜索的端到端耗时。
from typing import NotRequired, Protocol, TypedDict  # 声明节点状态与可替换应用服务边界。

from langgraph.graph import END, START, StateGraph  # 使用 LangGraph 定义有界循环和条件路由。

from backend.app.core.logging import logger  # 记录不含完整查询的节点失败堆栈和轮次统计。
from backend.app.models.coverage import CoverageReport  # 保存累计候选的覆盖判断与停止原因。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 保持网页补充发现与论文结果分离。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 回传 REST 与 SSE 共用的最终结果契约。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 保存单轮召回、排序和核验完成后的结果。
from backend.app.models.paper import PaperRecord, PaperSource  # 保存跨轮身份去重后的论文与来源状态。
from backend.app.models.query_evolution import QueryEvolutionResult  # 保存缺口驱动的安全补充查询结果。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 接收执行意图并维护待执行子查询。
from backend.app.models.search_run import SearchRunState  # 保存可恢复、可持久化的运行状态。
from backend.app.services.multi_round_search import _accumulate_source_counts, _append_pending_subqueries, _append_sources, _degraded_sources, _merge_round_papers, _query_for_subquery  # 复用既有身份、来源和子查询领域规则。
from backend.app.services.search_events import SearchRunEventPublisher  # 沿用安全进度事件发布边界。


class MultiRoundSearchExecutor(Protocol):
    """定义 LangGraph 节点使用的多轮搜索应用服务边界。"""

    def max_rounds_for(self, query: QueryIntent) -> int:
        """返回当前搜索模式允许的硬轮次上限。"""
        ...  # 轮次策略仍由服务层配置和校验。

    async def recall_once(self, query: QueryIntent) -> MultiSourceRecallResult:
        """执行一轮已完成路由、召回、排序和核验的搜索。"""
        ...  # 节点不直接承载 HTTP、认证或供应商响应解析。

    def analyze_coverage(self, query: QueryIntent, papers: list[PaperRecord], *, new_valid_count: int, source_counts: dict[str, int], unavailable_sources: tuple[str, ...], current_round: int, max_rounds: int, budget_exhausted: bool, has_executable_query: bool) -> CoverageReport:
        """按累计候选计算可解释覆盖报告与停止原因。"""
        ...  # 覆盖服务不调用外部 API。

    def evolve_query(self, query: QueryIntent, coverage_report: CoverageReport, *, executed_subqueries: list[str]) -> QueryEvolutionResult:
        """根据覆盖缺口生成有限且去重的补充子查询。"""
        ...  # 演化服务不放宽用户硬约束。

    def persist_state(self, state: SearchRunState) -> None:
        """尽力持久化运行轻量快照。"""
        ...  # 存储失败不应改变搜索控制流。

    def publish_event(self, publisher: SearchRunEventPublisher | None, state: SearchRunState, event_type: str, node: str, message: str, *, current_round: int | None = None, progress: float | None = None, metrics: dict[str, int | float | str | bool] | None = None) -> None:
        """发布不包含查询正文或论文摘要的轻量进度事件。"""
        ...  # 发布器可替换为 SSE 或 Redis 实现。


class SearchWorkflowState(TypedDict):
    """保存一次有界多轮搜索在 LangGraph 节点间传递的最小状态。"""

    query: QueryIntent  # 保存最初的完整意图，覆盖评估始终以它为准。
    current_query: QueryIntent  # 保存下一次来源召回实际使用的主查询或子查询。
    run_state: SearchRunState  # 保存可恢复、可持久化的运行状态。
    pending_subqueries: list[QuerySubquery]  # 保存尚未执行的原计划和演化子查询。
    executed_subqueries: list[str]  # 保存已执行文本以禁止跨轮重复检索。
    accumulated_papers: list[PaperRecord]  # 保存身份去重后按首次排序稳定排列的候选。
    paper_index: dict[str, int]  # 保存论文身份到累计位置的索引。
    discoveries: list[SupplementalDiscoveryItem]  # 保存独立网页补充发现，绝不并入论文集合。
    source_counts: dict[str, int]  # 保存跨轮累计来源成功数量。
    source_errors: dict[str, str]  # 保存来源最后一次安全错误摘要。
    selected_sources: list[PaperSource]  # 保存实际参与检索的学术来源顺序。
    coverage_report: CoverageReport | None  # 保存当前累计候选的覆盖报告。
    budget_exhausted: bool  # 保存调用前已确定的预算状态。
    started_at: float  # 保存 HTTP 入口开始处理本次搜索时的单调时钟时间点。
    event_publisher: SearchRunEventPublisher | None  # 透传 SSE 或未来 Redis 事件发布器。
    should_stop: bool  # 保存条件边是否应进入结果整理节点。
    round_result: NotRequired[MultiSourceRecallResult]  # 仅在召回节点成功后保存本轮结果。
    result: NotRequired[MultiRoundSearchResult]  # 仅在结果整理节点保存最终响应。


class SearchWorkflowError(RuntimeError):
    """表示 LangGraph 节点未能形成可返回的稳定搜索结果。"""


class MultiRoundSearchWorkflow:
    """以有界条件图执行初始化、召回、覆盖评估、查询演化和结果整理。"""

    def __init__(self, executor: MultiRoundSearchExecutor) -> None:
        """保存应用服务并编译固定节点与条件边。

        参数：
            executor：提供单轮搜索、覆盖、演化、状态存储和事件发布边界的服务。
        """
        self._executor = executor  # 工作流只依赖协议，不直接绑定来源适配器或模型。
        self._graph = self._build_graph()  # 在装配时编译有界循环图，避免每次请求重复定义节点。

    async def run(self, query: QueryIntent, *, budget_exhausted: bool = False, event_publisher: SearchRunEventPublisher | None = None, started_at: float | None = None) -> MultiRoundSearchResult:
        """执行有界条件图并返回与原 API 一致的多轮搜索结果。"""
        final_state = await self._graph.ainvoke({"query": query, "budget_exhausted": budget_exhausted, "event_publisher": event_publisher, "started_at": started_at if started_at is not None else perf_counter()})  # 从最小输入状态启动一次独立图运行。
        result = final_state.get("result")  # 仅读取结果整理节点写入的稳定响应。
        if not isinstance(result, MultiRoundSearchResult):  # 防止未来节点误删结果传播导致 API 返回空值。
            raise SearchWorkflowError("搜索工作流未生成最终结果")  # 返回不暴露内部状态的稳定编排错误。
        return result  # 保持 REST、SSE 和测试调用方的返回契约不变。

    def _build_graph(self):
        """创建带召回—评估—演化循环和显式停止条件的 LangGraph。"""
        graph = StateGraph(SearchWorkflowState)  # 使用明确 TypedDict 管理节点输入与输出。
        graph.add_node("initialize_run", self._initialize_run)  # 创建状态快照并发布运行创建事件。
        graph.add_node("parallel_search", self._parallel_search)  # 执行一次多源召回、融合、排序和核验。
        graph.add_node("assess_coverage", self._assess_coverage)  # 合并累计候选、评估覆盖并判断硬停止条件。
        graph.add_node("evolve_query", self._evolve_query)  # 在仍可继续时生成并选择下一条唯一子查询。
        graph.add_node("compose_results", self._compose_results)  # 统一发布最终事件并构造稳定结果响应。
        graph.add_edge(START, "initialize_run")  # 图从运行状态创建开始。
        graph.add_edge("initialize_run", "parallel_search")  # 初始化完成后才能调用来源。
        graph.add_edge("parallel_search", "assess_coverage")  # 每次召回后必须以累计候选重新评估覆盖。
        graph.add_conditional_edges("assess_coverage", self._route_after_assessment, {"stop": "compose_results", "evolve": "evolve_query"})  # 覆盖停止原因决定结束或进入查询演化。
        graph.add_conditional_edges("evolve_query", self._route_after_evolution, {"stop": "compose_results", "recall": "parallel_search"})  # 无新查询时结束，有唯一子查询时开始下一轮召回。
        graph.add_edge("compose_results", END)  # 只在最终响应已经构造后结束图运行。
        return graph.compile()  # 编译为可由 ainvoke 执行的异步图。

    async def _initialize_run(self, state: SearchWorkflowState) -> dict[str, object]:
        """创建首轮状态并在任何来源调用前持久化可恢复快照。"""
        query = state["query"]  # 读取实际执行意图。
        max_rounds = self._executor.max_rounds_for(query)  # 根据标准或深度模式取得已校验硬上限。
        run_state = SearchRunState(query_intent=query, search_mode=query.search_mode, max_rounds=max_rounds, status="running")  # 创建初始可恢复状态。
        self._executor.persist_state(run_state)  # 在外部来源调用前保存运行关联标识。
        self._executor.publish_event(state["event_publisher"], run_state, "run_created", "initialize_run", "已创建搜索运行", progress=0.0)  # 发送不含查询正文的首个进度事件。
        return {"current_query": query, "run_state": run_state, "pending_subqueries": list(query.subqueries), "executed_subqueries": [query.normalized_query], "accumulated_papers": [], "paper_index": {}, "discoveries": [], "source_counts": {}, "source_errors": {}, "selected_sources": [], "coverage_report": None, "should_stop": False}  # 初始化后续节点所需的受控状态。

    async def _parallel_search(self, state: SearchWorkflowState) -> dict[str, object]:
        """调用一轮多源搜索，内部故障时将状态安全转为可返回终态。"""
        run_state = state["run_state"]  # 读取上一节点持久化的最新状态。
        next_round = run_state.current_round + 1  # 计算即将开始的合法轮次编号。
        self._executor.publish_event(state["event_publisher"], run_state, "node_started", "parallel_search", "开始执行一轮多源检索", current_round=next_round, progress=(next_round - 1) / run_state.max_rounds)  # 在来源调用前发布节点开始事件。
        try:  # 保护工作流免受服务层未预期异常影响。
            round_result = await self._executor.recall_once(state["current_query"])  # 服务层继续隔离来源、模型和排序细节。
        except Exception:  # 内部错误不得触发重试循环或泄露实现细节。
            logger.exception("LangGraph 多轮搜索召回节点失败：轮次=%d", next_round)  # 记录安全轮次和完整受控堆栈。
            failed_state = run_state.model_copy(update={"status": "failed", "current_round": next_round, "stop_reason": "搜索执行出现内部错误", "errors": [*run_state.errors, "搜索执行出现内部错误"], "latency_ms": _elapsed_latency_ms(state["started_at"])})  # 构造可恢复失败状态。
            self._executor.persist_state(failed_state)  # 保存失败终态供轮询和恢复使用。
            self._executor.publish_event(state["event_publisher"], failed_state, "failed", "parallel_search", "搜索执行出现内部错误", current_round=next_round, progress=1.0)  # 向前端发布安全错误摘要。
            return {"run_state": failed_state, "should_stop": True}  # 由条件边进入统一结果整理节点。
        return {"round_result": round_result}  # 将已完成本轮服务结果交给覆盖节点处理。

    async def _assess_coverage(self, state: SearchWorkflowState) -> dict[str, object]:
        """合并本轮结果、持久化快照并依据覆盖报告决定是否停止。"""
        if state["should_stop"]:  # 召回节点失败时没有可评估的本轮结果。
            return {}  # 保持失败状态交由结果节点返回。
        round_result = state["round_result"]  # 读取召回节点提供的稳定单轮结果。
        run_state = state["run_state"]  # 读取上一轮状态。
        accumulated_papers = state["accumulated_papers"]  # 保持跨轮候选首次排序顺序。
        paper_index = state["paper_index"]  # 使用身份索引防止重复论文虚增收益。
        source_counts = state["source_counts"]  # 累计来源成功数量。
        source_errors = state["source_errors"]  # 累计来源安全错误摘要。
        selected_sources = state["selected_sources"]  # 记录实际参与的学术来源。
        new_valid_count = _merge_round_papers(accumulated_papers, paper_index, round_result.papers)  # 合并论文并计算真实新增数量。
        discoveries = [*state["discoveries"], *round_result.discoveries]  # 网页发现独立累积且不进入论文去重。
        _accumulate_source_counts(source_counts, round_result.source_counts)  # 合并每轮来源成功计数。
        source_errors.update(round_result.source_errors)  # 保留来源最后一次安全错误。
        _append_sources(selected_sources, round_result.route_plan.academic_sources)  # 按首次参与顺序记录学术来源。
        next_round = run_state.current_round + 1  # 当前轮次在评估成功后才正式写入状态。
        coverage_report = self._executor.analyze_coverage(state["query"], accumulated_papers, new_valid_count=new_valid_count, source_counts=source_counts, unavailable_sources=tuple(source_errors), current_round=next_round, max_rounds=run_state.max_rounds, budget_exhausted=state["budget_exhausted"], has_executable_query=True)  # 基于累计候选而非单轮结果判断覆盖。
        source_request_count = len(round_result.route_plan.academic_sources) + len(round_result.route_plan.web_discovery_sources)  # 统计本轮理论需要访问的来源数量。
        updated_state = run_state.model_copy(update={"current_round": next_round, "selected_sources": selected_sources, "executed_subqueries": state["executed_subqueries"], "normalized_papers": accumulated_papers, "candidate_ids": [paper.paper_id for paper in accumulated_papers], "final_papers": accumulated_papers, "api_call_count": run_state.api_call_count + max(0, source_request_count - round_result.cache_hit_count), "token_usage": run_state.token_usage + round_result.llm_prompt_tokens + round_result.llm_completion_tokens, "latency_ms": _elapsed_latency_ms(state["started_at"]), "cache_hits": run_state.cache_hits + round_result.cache_hit_count, "warnings": run_state.warnings, "errors": [*run_state.errors, *round_result.source_errors.values()], "degraded_sources": _degraded_sources(selected_sources, source_errors), "coverage_report": coverage_report})  # 写入可恢复统计与累计候选。
        self._executor.persist_state(updated_state)  # 在每轮完整统计形成后保存轻量快照。
        self._executor.publish_event(state["event_publisher"], updated_state, "node_completed", "assess_coverage", "本轮检索、核验和覆盖分析已完成", current_round=next_round, progress=next_round / updated_state.max_rounds, metrics={"new_valid_count": new_valid_count, "candidate_count": len(accumulated_papers), "source_error_count": len(source_errors)})  # 发布不含论文详情的轮次统计。
        logger.info("LangGraph 多轮搜索完成一轮：轮次=%d，新增高质量论文=%d，累计论文=%d，来源错误=%d，是否建议继续=%s", next_round, new_valid_count, len(accumulated_papers), len(source_errors), coverage_report.should_continue)  # 仅记录计数与布尔控制状态。
        if coverage_report.stop_reason is not None:  # 目标、预算、来源、边际收益或轮次上限触发时立即停止。
            completed_state = updated_state.model_copy(update={"status": "completed", "stop_reason": coverage_report.stop_reason})  # 保持当前最佳结果并设置可解释停止原因。
            self._executor.persist_state(completed_state)  # 保存正常终态供轮询、SSE 和恢复读取。
            return {"run_state": completed_state, "accumulated_papers": accumulated_papers, "paper_index": paper_index, "discoveries": discoveries, "source_counts": source_counts, "source_errors": source_errors, "selected_sources": selected_sources, "coverage_report": coverage_report, "should_stop": True}  # 由条件边直接进入结果整理。
        return {"run_state": updated_state, "accumulated_papers": accumulated_papers, "paper_index": paper_index, "discoveries": discoveries, "source_counts": source_counts, "source_errors": source_errors, "selected_sources": selected_sources, "coverage_report": coverage_report, "should_stop": False}  # 进入查询演化节点尝试补足缺口。

    async def _evolve_query(self, state: SearchWorkflowState) -> dict[str, object]:
        """生成下一轮唯一子查询；无法继续时重新计算准确停止原因。"""
        coverage_report = state["coverage_report"]  # 读取覆盖节点已经生成的报告。
        if coverage_report is None:  # 防御图配置错误或缺失状态传播。
            raise SearchWorkflowError("覆盖评估节点未生成报告")  # 阻止缺少停止条件的循环继续执行。
        evolution_result = self._executor.evolve_query(state["query"], coverage_report, executed_subqueries=state["executed_subqueries"])  # 仅针对当前缺口生成去重补充查询。
        pending_subqueries = _append_pending_subqueries(state["pending_subqueries"], evolution_result.generated_subqueries, state["executed_subqueries"])  # 保留原计划优先并追加不重复演化查询。
        if not pending_subqueries:  # 没有可执行新查询时不能重复首轮表达。
            run_state = state["run_state"]  # 读取本轮已持久化状态。
            final_report = self._executor.analyze_coverage(state["query"], state["accumulated_papers"], new_valid_count=coverage_report.new_valid_count, source_counts=state["source_counts"], unavailable_sources=tuple(state["source_errors"]), current_round=run_state.current_round, max_rounds=run_state.max_rounds, budget_exhausted=state["budget_exhausted"], has_executable_query=False)  # 重算“没有可执行新查询”而非伪造其他停止原因。
            completed_state = run_state.model_copy(update={"status": "completed", "stop_reason": final_report.stop_reason, "coverage_report": final_report, "latency_ms": _elapsed_latency_ms(state["started_at"]), "warnings": [*run_state.warnings, *evolution_result.warnings]})  # 保存演化跳过提示与最终报告。
            self._executor.persist_state(completed_state)  # 保存无新查询时的完成状态。
            return {"run_state": completed_state, "pending_subqueries": pending_subqueries, "coverage_report": final_report, "should_stop": True}  # 由条件边进入结果整理。
        next_subquery = pending_subqueries.pop(0)  # 按 Query Agent 原计划优先、再按缺口严重度选择下一条查询。
        executed_subqueries = [*state["executed_subqueries"], next_subquery.query]  # 在下一轮来源调用前标记已执行以防恢复后重复。
        return {"current_query": _query_for_subquery(state["query"], next_subquery), "pending_subqueries": pending_subqueries, "executed_subqueries": executed_subqueries, "should_stop": False}  # 回到召回节点执行唯一补充表达。

    async def _compose_results(self, state: SearchWorkflowState) -> dict[str, object]:
        """发布终态事件并构造不额外调用来源的稳定最终结果。"""
        run_state = state["run_state"].model_copy(update={"latency_ms": _elapsed_latency_ms(state["started_at"])})  # 在终态事件与结果快照前更新完整端到端耗时。
        self._executor.persist_state(run_state)  # 将最终耗时写回 SQLite 快照供后续只读用量接口读取。
        self._executor.publish_event(state["event_publisher"], run_state, "completed" if run_state.status == "completed" else "failed", "compose_results", run_state.stop_reason or "搜索已完成", progress=1.0, metrics={"final_paper_count": len(state["accumulated_papers"])})  # 统一发布一次终态事件。
        result = MultiRoundSearchResult(run_state=run_state, query_intent=state["query"], papers=state["accumulated_papers"], discoveries=state["discoveries"], source_counts=state["source_counts"], source_errors=state["source_errors"], coverage_report=state["coverage_report"])  # 复用原 API 契约且不额外检索。
        return {"result": result}  # 写入唯一最终结果供 run 方法读取。

    def _route_after_assessment(self, state: SearchWorkflowState) -> str:
        """根据覆盖节点停止判断选择结果整理或查询演化分支。"""
        return "stop" if state["should_stop"] else "evolve"  # 仅由已持久化覆盖报告驱动条件边。

    def _route_after_evolution(self, state: SearchWorkflowState) -> str:
        """根据是否生成唯一补充查询选择结束或下一轮召回。"""
        return "stop" if state["should_stop"] else "recall"  # 无可执行查询时绝不回到首轮表达。


def _elapsed_latency_ms(started_at: float) -> int:
    """返回从 HTTP 入口开始到当前节点的非负端到端耗时毫秒数。"""

    return max(0, int((perf_counter() - started_at) * 1000))  # 防御极少数计时精度边界，保持 API 契约非负。
