"""使用 LangGraph 编排多轮召回、覆盖评估和查询演化。"""

from time import perf_counter  # 使用单调高精度时钟统计一次搜索的端到端耗时。
from typing import NotRequired, Protocol, TypedDict  # 声明节点状态与可替换应用服务边界。

from langgraph.graph import END, START, StateGraph  # 使用 LangGraph 定义有界循环和条件路由。

from backend.app.core.logging import logger  # 记录不含完整查询的节点失败堆栈和轮次统计。
from backend.app.models.candidate_generation import CandidateGenerationResult  # 保存每轮严格停在 BGE-M3 前的候选结果。
from backend.app.models.coverage import CoverageReport  # 保存累计候选的覆盖判断与停止原因。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 保持网页补充发现与论文结果分离。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 回传 REST 与 SSE 共用的最终结果契约。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 保存终态一次排序、核验和覆盖完成后的结果。
from backend.app.models.paper import PaperRecord, PaperSource  # 保存跨轮身份去重后的论文与来源状态。
from backend.app.models.query_evolution import QueryEvolutionResult  # 保存缺口驱动的安全补充查询结果。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 接收执行意图并维护待执行子查询。
from backend.app.models.source_routing import SourceRoutePlan  # 在终态聚合各轮真实来源计划。
from backend.app.models.search_run import SearchRunState  # 保存可恢复、可持久化的运行状态。
from backend.app.services.multi_round_search import _accumulate_source_counts, _append_pending_subqueries, _append_sources, _degraded_sources, _merge_round_papers, _query_for_round, _query_for_subquery, _remaining_source_recall_count  # 复用既有身份、来源、轮次和子查询领域规则。
from backend.app.services.search_events import SearchRunEventPublisher  # 沿用安全进度事件发布边界。


class MultiRoundSearchExecutor(Protocol):
    """定义 LangGraph 节点使用的多轮搜索应用服务边界。"""

    def max_rounds_for(self, query: QueryIntent) -> int:
        """返回当前搜索模式允许的硬轮次上限。"""
        ...  # 轮次策略仍由服务层配置和校验。

    async def recall_candidates_once(self, query: QueryIntent) -> CandidateGenerationResult:
        """执行一轮来源召回、融合和规则过滤，不加载排序模型或调用 LLM。"""
        ...  # 节点不直接承载 HTTP、认证或供应商响应解析。

    async def finalize_candidates(self, candidate_result: CandidateGenerationResult) -> MultiSourceRecallResult:
        """对全部轮次聚合候选执行唯一一次分层排序、核验和覆盖分析。"""
        ...  # 节点不直接承载 HTTP、认证或供应商响应解析。

    def analyze_coverage(self, query: QueryIntent, papers: list[PaperRecord], *, new_valid_count: int, source_counts: dict[str, int], unavailable_sources: tuple[str, ...], current_round: int, max_rounds: int, budget_exhausted: bool, has_executable_query: bool) -> CoverageReport:
        """按累计候选计算可解释覆盖报告与停止原因。"""
        ...  # 覆盖服务不调用外部 API。

    async def evolve_query(self, query: QueryIntent, coverage_report: CoverageReport, *, papers: list[PaperRecord], executed_subqueries: list[str]) -> QueryEvolutionResult:
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
    candidate_results: list[CandidateGenerationResult]  # 保存每轮候选审计，用于终态构造可校验聚合边界。
    coverage_report: CoverageReport | None  # 保存当前累计候选的覆盖报告。
    budget_exhausted: bool  # 保存调用前已确定的预算状态。
    started_at: float  # 保存 HTTP 入口开始处理本次搜索时的单调时钟时间点。
    event_publisher: SearchRunEventPublisher | None  # 透传 SSE 或未来 Redis 事件发布器。
    should_stop: bool  # 保存条件边是否应进入结果整理节点。
    round_result: NotRequired[CandidateGenerationResult]  # 仅在候选召回节点成功后保存本轮结果。
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
        graph.add_node("parallel_search", self._parallel_search)  # 执行一次多源召回、融合和规则过滤。
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
        return {"current_query": _query_for_round(query, retrieval_round=1), "run_state": run_state, "pending_subqueries": list(query.subqueries), "executed_subqueries": [query.normalized_query], "accumulated_papers": [], "paper_index": {}, "discoveries": [], "source_counts": {}, "source_errors": {}, "selected_sources": [], "candidate_results": [], "coverage_report": None, "should_stop": False}  # 初始化后续节点所需的受控状态，并让来源路由器识别首轮。

    async def _parallel_search(self, state: SearchWorkflowState) -> dict[str, object]:
        """调用一轮排序前候选生成，内部故障时将状态安全转为可返回终态。"""
        run_state = state["run_state"]  # 读取上一节点持久化的最新状态。
        next_round = run_state.current_round + 1  # 计算即将开始的合法轮次编号。
        self._executor.publish_event(state["event_publisher"], run_state, "node_started", "parallel_search", "开始执行一轮多源检索", current_round=next_round, progress=(next_round - 1) / run_state.max_rounds)  # 在来源调用前发布节点开始事件。
        try:  # 保护工作流免受服务层未预期异常影响。
            round_result = await self._executor.recall_candidates_once(state["current_query"])  # 每轮严格停在来源、融合和规则过滤边界。
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
        new_valid_count = _merge_round_papers(accumulated_papers, paper_index, round_result.papers)  # 合并排序前论文并计算真实新增候选数量。
        discoveries = [*state["discoveries"], *round_result.discoveries]  # 网页发现独立累积且不进入论文去重。
        _accumulate_source_counts(source_counts, round_result.source_counts)  # 合并每轮来源成功计数。
        source_errors.update(round_result.source_errors)  # 保留来源最后一次安全错误。
        _append_sources(selected_sources, round_result.route_plan.academic_sources)  # 按首次参与顺序记录学术来源。
        next_round = run_state.current_round + 1  # 当前轮次在评估成功后才正式写入状态。
        coverage_report = self._executor.analyze_coverage(state["query"], accumulated_papers, new_valid_count=new_valid_count, source_counts=source_counts, unavailable_sources=tuple(source_errors), current_round=next_round, max_rounds=run_state.max_rounds, budget_exhausted=state["budget_exhausted"], has_executable_query=True)  # 基于累计候选而非单轮结果判断覆盖。
        source_request_count = len(round_result.route_plan.academic_sources) + len(round_result.route_plan.web_discovery_sources)  # 统计本轮理论需要访问的来源数量。
        updated_state = run_state.model_copy(update={"current_round": next_round, "selected_sources": selected_sources, "executed_subqueries": state["executed_subqueries"], "normalized_papers": accumulated_papers, "candidate_ids": [paper.paper_id for paper in accumulated_papers], "final_papers": [], "api_call_count": run_state.api_call_count + max(0, source_request_count - round_result.cache_hit_count), "latency_ms": _elapsed_latency_ms(state["started_at"]), "cache_hits": run_state.cache_hits + round_result.cache_hit_count, "warnings": run_state.warnings, "errors": [*run_state.errors, *round_result.source_errors.values()], "degraded_sources": _degraded_sources(selected_sources, source_errors), "coverage_report": coverage_report})  # 中间快照只保存候选，不得将未排序论文伪装为最终结果或累计 LLM 用量。
        self._executor.persist_state(updated_state)  # 在每轮完整统计形成后保存轻量快照。
        self._executor.publish_event(state["event_publisher"], updated_state, "node_completed", "assess_coverage", "本轮候选生成和覆盖分析已完成", current_round=next_round, progress=next_round / updated_state.max_rounds, metrics={"new_candidate_count": new_valid_count, "candidate_count": len(accumulated_papers), "source_error_count": len(source_errors)})  # 发布不含论文详情的轮次候选统计。
        logger.info("LangGraph 多轮候选生成完成：轮次=%d，新增候选=%d，累计候选=%d，来源错误=%d，是否建议继续=%s", next_round, new_valid_count, len(accumulated_papers), len(source_errors), coverage_report.should_continue)  # 排序和核验仅留待终态执行一次。
        if coverage_report.stop_reason is not None:  # 目标、预算、来源、边际收益或轮次上限触发时立即停止。
            completed_state = updated_state.model_copy(update={"status": "completed", "stop_reason": coverage_report.stop_reason})  # 保持当前最佳结果并设置可解释停止原因。
            self._executor.persist_state(completed_state)  # 保存正常终态供轮询、SSE 和恢复读取。
            return {"run_state": completed_state, "accumulated_papers": accumulated_papers, "paper_index": paper_index, "discoveries": discoveries, "source_counts": source_counts, "source_errors": source_errors, "selected_sources": selected_sources, "candidate_results": [*state["candidate_results"], round_result], "coverage_report": coverage_report, "should_stop": True}  # 由条件边直接进入唯一终态排序节点。
        return {"run_state": updated_state, "accumulated_papers": accumulated_papers, "paper_index": paper_index, "discoveries": discoveries, "source_counts": source_counts, "source_errors": source_errors, "selected_sources": selected_sources, "candidate_results": [*state["candidate_results"], round_result], "coverage_report": coverage_report, "should_stop": False}  # 进入查询演化节点尝试补足候选覆盖。

    async def _evolve_query(self, state: SearchWorkflowState) -> dict[str, object]:
        """生成下一轮唯一子查询；无法继续时重新计算准确停止原因。"""
        coverage_report = state["coverage_report"]  # 读取覆盖节点已经生成的报告。
        if coverage_report is None:  # 防御图配置错误或缺失状态传播。
            raise SearchWorkflowError("覆盖评估节点未生成报告")  # 阻止缺少停止条件的循环继续执行。
        evolution_result = await self._executor.evolve_query(state["query"], coverage_report, papers=state["accumulated_papers"], executed_subqueries=state["executed_subqueries"])  # 基于当前论文证据与缺口生成去重补充查询。
        if evolution_result.strategy_model_name is not None:  # 仅在 LLM 已实际产出有效策略时让其优先决定下一轮表达。
            strategy_subqueries = _append_pending_subqueries([], evolution_result.generated_subqueries, state["executed_subqueries"])  # 先对策略提案执行既有去重保护。
            pending_subqueries = _append_pending_subqueries(strategy_subqueries, state["pending_subqueries"], state["executed_subqueries"])  # 再保留尚未执行的初始计划作为后备。
        else:  # 规则回退和未调用策略时维持原有计划优先级，避免改变离线流程。
            pending_subqueries = _append_pending_subqueries(state["pending_subqueries"], evolution_result.generated_subqueries, state["executed_subqueries"])  # 追加不重复的确定性演化查询。
        run_state = state["run_state"].model_copy(update={"token_usage": state["run_state"].token_usage + evolution_result.strategy_prompt_tokens + evolution_result.strategy_completion_tokens, "estimated_cost_cny": round(state["run_state"].estimated_cost_cny + evolution_result.strategy_estimated_cost_cny, 8), "peak_pricing_applied": state["run_state"].peak_pricing_applied or evolution_result.strategy_peak_pricing_applied, "warnings": [*state["run_state"].warnings, *evolution_result.warnings]})  # 将策略模型 Token、费用与安全降级摘要写入可恢复运行状态。
        self._executor.persist_state(run_state)  # 在下一轮来源调用前持久化策略成本和降级信息。
        next_round = run_state.current_round + 1  # 下一轮只能位于已校验的最大轮次范围内。
        next_source_recall_count = _remaining_source_recall_count(state["query"], coverage_report) if next_round == 3 else state["query"].source_recall_count  # 第三轮只按尚缺高相关论文数请求单源候选，其余轮保持规划召回规模。
        if not pending_subqueries:  # 没有可执行新查询时不能重复首轮表达。
            if next_round == 3 and coverage_report.high_relevance_count < state["query"].target_paper_count:  # 两轮后仍不足目标时允许第三轮切换来源，不因缺少子查询错过补足机会。
                fallback_query = _query_for_round(state["query"], retrieval_round=next_round, source_recall_count=next_source_recall_count)  # 使用完整原始约束向第三相关来源请求恰好覆盖缺口的候选规模。
                return {"run_state": run_state, "current_query": fallback_query, "pending_subqueries": pending_subqueries, "should_stop": False}  # 直接回到召回节点执行唯一补足轮，并保留策略审计状态。
            final_report = self._executor.analyze_coverage(state["query"], state["accumulated_papers"], new_valid_count=coverage_report.new_valid_count, source_counts=state["source_counts"], unavailable_sources=tuple(state["source_errors"]), current_round=run_state.current_round, max_rounds=run_state.max_rounds, budget_exhausted=state["budget_exhausted"], has_executable_query=False)  # 重算“没有可执行新查询”而非伪造其他停止原因。
            completed_state = run_state.model_copy(update={"status": "completed", "stop_reason": final_report.stop_reason, "coverage_report": final_report, "latency_ms": _elapsed_latency_ms(state["started_at"]), "warnings": [*run_state.warnings, *evolution_result.warnings]})  # 保存演化跳过提示与最终报告。
            self._executor.persist_state(completed_state)  # 保存无新查询时的完成状态。
            return {"run_state": completed_state, "pending_subqueries": pending_subqueries, "coverage_report": final_report, "should_stop": True}  # 由条件边进入结果整理。
        next_subquery = pending_subqueries.pop(0)  # 按 Query Agent 原计划优先、再按缺口严重度选择下一条查询。
        executed_subqueries = [*state["executed_subqueries"], next_subquery.query]  # 在下一轮来源调用前标记已执行以防恢复后重复。
        return {"run_state": run_state, "current_query": _query_for_subquery(state["query"], next_subquery, retrieval_round=next_round, source_recall_count=next_source_recall_count), "pending_subqueries": pending_subqueries, "executed_subqueries": executed_subqueries, "should_stop": False}  # 回到召回节点执行唯一补充表达，并保留策略用量进入后续轮次。

    async def _compose_results(self, state: SearchWorkflowState) -> dict[str, object]:
        """在候选累计结束后统一排序、核验并构造稳定最终结果。"""
        if state["run_state"].status == "failed":  # 候选生成失败时不得错误触发本地模型或 DeepSeek。
            final_papers: list[PaperRecord] = []  # 失败运行不伪造最终论文。
            final_coverage = state["coverage_report"]  # 保留失败前最后一个安全覆盖摘要。
            final_ranking: MultiSourceRecallResult | None = None  # 明确本路径没有终态排序审计。
        else:
            aggregated_candidates = _aggregate_candidate_results(state["query"], state["candidate_results"], state["accumulated_papers"], state["discoveries"])  # 按跨轮身份去重后的候选构造可校验终态排序输入。
            final_ranking = await self._executor.finalize_candidates(aggregated_candidates)  # BGE、Cross Encoder 与 DeepSeek 只在此处各执行一次。
            final_papers = final_ranking.papers  # 只将终态核验后的论文写入最终响应和 SQLite 快照。
            preserve_hard_stop_reason = state["run_state"].stop_reason in {"搜索预算已达到上限", "已达到最大搜索轮次", "可用学术来源不足"}  # 预算、轮次和全部来源不可用已在候选阶段确认，终态核验不得改写为查询演化不足。
            final_coverage = self._executor.analyze_coverage(state["query"], final_papers, new_valid_count=len(final_papers), source_counts=state["source_counts"], unavailable_sources=tuple(state["source_errors"]), current_round=state["run_state"].current_round, max_rounds=state["run_state"].max_rounds, budget_exhausted=state["budget_exhausted"], has_executable_query=preserve_hard_stop_reason)  # 使用最终高相关论文重算展示覆盖；已确认硬停止时不让“无新查询”抢占其原因。
        run_state = state["run_state"].model_copy(update={"status": "completed" if state["run_state"].status != "failed" else "failed", "stop_reason": final_coverage.stop_reason if final_coverage is not None and final_coverage.stop_reason is not None else state["run_state"].stop_reason, "final_papers": final_papers, "token_usage": state["run_state"].token_usage + (0 if final_ranking is None else final_ranking.llm_prompt_tokens + final_ranking.llm_completion_tokens), "estimated_cost_cny": round(state["run_state"].estimated_cost_cny + (0.0 if final_ranking is None else final_ranking.llm_estimated_cost_cny), 8), "peak_pricing_applied": state["run_state"].peak_pricing_applied or (False if final_ranking is None else final_ranking.llm_peak_pricing_applied), "latency_ms": _elapsed_latency_ms(state["started_at"]), "coverage_report": final_coverage, "warnings": state["run_state"].warnings, "errors": state["run_state"].errors})  # 仅在终态合并一次 LLM 用量、成本、结果和最终覆盖报告。
        self._executor.persist_state(run_state)  # 将最终耗时写回 SQLite 快照供后续只读用量接口读取。
        self._executor.publish_event(state["event_publisher"], run_state, "completed" if run_state.status == "completed" else "failed", "compose_results", run_state.stop_reason or "搜索已完成", progress=1.0, metrics={"final_paper_count": len(final_papers)})  # 统一发布一次终态事件，数量只使用最终核验论文。
        result = MultiRoundSearchResult(run_state=run_state, query_intent=state["query"], papers=final_papers, discoveries=state["discoveries"], source_counts=state["source_counts"], source_errors=state["source_errors"], coverage_report=final_coverage)  # 复用原 API 契约且不额外检索。
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


def _aggregate_candidate_results(query: QueryIntent, round_results: list[CandidateGenerationResult], papers: list[PaperRecord], discoveries: list[SupplementalDiscoveryItem]) -> CandidateGenerationResult:
    """将各轮排序前候选合并为一次可校验的终态排序输入。

    参数：
        query：用于终态 BGE、Cross Encoder 和 LLM 核验的原始完整约束。
        round_results：每轮已完成来源、融合和规则过滤的可审计候选结果。
        papers：跨轮按身份去重后的最终排序输入候选。
        discoveries：跨轮累积但始终不进入论文排序的网页发现项。
    返回：
        CandidateGenerationResult：保留跨轮来源审计且严格停在排序模型之前的聚合边界。
    异常：
        SearchWorkflowError：没有成功候选轮次时抛出，阻止构造虚假的路由计划。
    """
    if not round_results:  # 正常完成至少应含一轮候选生成，空集合只能来自编排错误。
        raise SearchWorkflowError("搜索工作流未生成可供终态排序的候选")  # 不使用伪造来源或空计划掩盖节点状态错误。
    academic_sources: list[PaperSource] = []  # 按首次参与顺序保留全部真实学术来源。
    web_sources: list[str] = []  # 网页发现来源保持独立路由类别，最终只用于审计。
    selection_reasons: dict[str, str] = {}  # 合并每轮可展示的来源选择理由。
    unavailable_reasons: dict[str, str] = {}  # 合并每轮不启用来源的安全说明。
    academic_source_counts: dict[str, int] = {}  # 累计每个真实学术来源映射的论文数量。
    web_discovery_source_counts: dict[str, int] = {}  # 累计每个补充网页来源的发现数量。
    academic_source_errors: dict[str, str] = {}  # 保留各学术来源最后一次安全错误摘要。
    web_discovery_source_errors: dict[str, str] = {}  # 保留各网页来源最后一次安全错误摘要。
    filter_reason_counts: dict[str, int] = {}  # 汇总各轮确定性过滤的首个失败原因。
    cache_hit_count = 0  # 累计各轮来源响应缓存命中。
    normalized_candidate_count = 0  # 累计各轮进入融合前的统一论文数量。
    filtered_candidate_count = 0  # 累计各轮由确定性规则移除的论文数量。
    for result in round_results:  # 保持轮次顺序汇总来源和阶段审计。
        for source_name in result.route_plan.academic_sources:  # 学术来源可跨轮复用但终态计划只记录一次。
            if source_name not in academic_sources:  # 首次出现决定稳定展示顺序。
                academic_sources.append(source_name)  # 保留实际进入论文候选的来源。
        for source_name in result.route_plan.web_discovery_sources:  # 网页来源也按首次出现顺序保留。
            if source_name not in web_sources:  # 防止跨轮重复来源污染路由契约。
                web_sources.append(source_name)  # 保留独立网页发现来源。
        selection_reasons.update(result.route_plan.selection_reasons)  # 同名来源以最近实际轮次的理由为准。
        unavailable_reasons.update(result.route_plan.unavailable_reasons)  # 保留可展示的未启用说明。
        _accumulate_source_counts(academic_source_counts, result.academic_source_counts)  # 汇总学术来源数量且不混入网页发现。
        _accumulate_source_counts(web_discovery_source_counts, result.web_discovery_source_counts)  # 汇总网页发现数量且不混入论文。
        academic_source_errors.update(result.academic_source_errors)  # 保留最后一次安全学术来源错误。
        web_discovery_source_errors.update(result.web_discovery_source_errors)  # 保留最后一次安全网页来源错误。
        _accumulate_source_counts(filter_reason_counts, result.filter_reason_counts)  # 复用非负计数累加规则汇总过滤原因。
        cache_hit_count += result.cache_hit_count  # 缓存命中按真实来源调用轮次累积。
        normalized_candidate_count += result.normalized_candidate_count  # 保留供应商映射后的真实候选分母。
        filtered_candidate_count += result.filtered_candidate_count  # 保留规则过滤审计分母。
    deduplicated_candidate_count = filtered_candidate_count + len(papers)  # 跨轮身份去重后，候选只能进入规则过滤或终态排序输入二者之一。
    merged_candidate_count = normalized_candidate_count - deduplicated_candidate_count  # 剩余数量恰为轮内与跨轮身份融合合并的记录。
    if merged_candidate_count < 0:  # 防御未来统计字段改变导致无法解释的阶段数量关系。
        raise SearchWorkflowError("跨轮候选统计无法满足融合边界")  # 阻止将不一致计数交给终态模型调用。
    route_plan = SourceRoutePlan(academic_sources=academic_sources, web_discovery_sources=web_sources, selection_reasons=selection_reasons, unavailable_reasons=unavailable_reasons)  # 构造覆盖全部真实来源的终态可审计路由。
    return CandidateGenerationResult(  # 继续复用统一候选契约，避免终态排序绕过阶段边界。
        route_plan=route_plan,
        query_intent=query,
        papers=papers,
        discoveries=discoveries,
        academic_source_counts=academic_source_counts,
        web_discovery_source_counts=web_discovery_source_counts,
        academic_source_errors=academic_source_errors,
        web_discovery_source_errors=web_discovery_source_errors,
        cache_hit_count=cache_hit_count,
        normalized_candidate_count=normalized_candidate_count,
        deduplicated_candidate_count=deduplicated_candidate_count,
        merged_candidate_count=merged_candidate_count,
        filtered_candidate_count=filtered_candidate_count,
        filter_reason_counts=filter_reason_counts,
        work_family_count=len({paper.work_family_id for paper in papers if paper.work_family_id}),
    )
