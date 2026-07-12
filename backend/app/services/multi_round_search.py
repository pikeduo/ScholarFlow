"""协调多轮召回、覆盖分析、查询演化与保护性停止条件。"""

from collections.abc import Sequence  # 接收不可变的已执行查询和论文候选序列。
from typing import Protocol  # 定义可离线替换的单轮召回边界。

from backend.app.core.logging import logger  # 记录不含完整查询文本的轮次统计与异常堆栈。
from backend.app.models.coverage import CoverageReport  # 保存跨轮累计候选的覆盖判断。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 构造控制器稳定返回契约。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 消费单轮协调器的已排序结果。
from backend.app.models.paper import PaperRecord, PaperSource  # 保存跨轮去重论文和来源状态。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 构造轮次专用查询并消费待执行子查询。
from backend.app.models.search_run import SearchRunState  # 更新可恢复的搜索运行状态。
from backend.app.services.coverage_analysis import CoverageGapAnalyzer  # 以累计候选重新计算缺口与停止原因。
from backend.app.services.query_evolution import QueryEvolutionService  # 在存在缺口时生成去重补充子查询。


class SingleRoundRecallCoordinator(Protocol):
    """定义多轮控制器所需的单轮召回能力，隔离具体来源与排序实现。"""

    async def recall(self, query: QueryIntent) -> MultiSourceRecallResult:
        """执行一轮已完成排序和核验的多源检索并返回稳定结果。"""
        ...  # 协议只声明边界，不承载实际 HTTP 或模型逻辑。


class MultiRoundSearchController:
    """执行有限轮次的科研检索，并基于覆盖与收益在安全条件下停止。"""

    def __init__(
        self,
        coordinator: SingleRoundRecallCoordinator,
        coverage_gap_analyzer: CoverageGapAnalyzer | None = None,
        query_evolution_service: QueryEvolutionService | None = None,
        standard_max_rounds: int = 2,
        deep_max_rounds: int = 3,
    ) -> None:
        """保存可替换协作者与两种搜索模式的硬轮次上限。

        参数：
            coordinator：执行单轮来源召回和分层排序的可替换协调器。
            coverage_gap_analyzer：分析累计候选覆盖并给出继续或停止建议的服务。
            query_evolution_service：根据缺口生成去重补充子查询的服务。
            standard_max_rounds：标准模式允许的最大轮次。
            deep_max_rounds：深度模式允许的最大轮次。
        异常：
            ValueError：轮次上限不在一到三之间或深度模式小于标准模式时抛出。
        """
        if not 1 <= standard_max_rounds <= 3:  # 标准模式必须保留至少首轮且符合 SearchRunState 上限。
            raise ValueError("standard_max_rounds 必须位于 [1, 3] 区间")  # 防止控制器构造非法运行状态。
        if not standard_max_rounds <= deep_max_rounds <= 3:  # 深度模式不得比标准模式更短也不得超过状态契约上限。
            raise ValueError("deep_max_rounds 必须位于 [standard_max_rounds, 3] 区间")  # 保持两种模式的成本语义明确。
        self._coordinator = coordinator  # 保存不绑定具体适配器、模型或 API 的单轮边界。
        self._coverage_gap_analyzer = coverage_gap_analyzer or CoverageGapAnalyzer()  # 默认使用纯本地覆盖分析。
        self._query_evolution_service = query_evolution_service or QueryEvolutionService()  # 默认使用不调用外部服务的确定性查询演化。
        self._standard_max_rounds = standard_max_rounds  # 保存标准模式防无限循环的上限。
        self._deep_max_rounds = deep_max_rounds  # 保存深度模式允许的额外缺口修复轮次。

    async def run(self, query: QueryIntent, *, budget_exhausted: bool = False) -> MultiRoundSearchResult:
        """从首轮主查询开始执行多轮检索，直到达到目标或触发保护性停止。

        参数：
            query：已完成自然语言规划或用户编辑的结构化搜索意图。
            budget_exhausted：调用前已知的 API、Token、费用或耗时预算触顶状态。
        返回：
            MultiRoundSearchResult：包含跨轮去重候选、最终覆盖报告和运行状态。
        """
        max_rounds = self._deep_max_rounds if query.search_mode == "deep" else self._standard_max_rounds  # 按模式选择明确且有限的成本预算。
        state = SearchRunState(query_intent=query, search_mode=query.search_mode, max_rounds=max_rounds, status="running")  # 创建可被后续持久化和恢复的初始状态。
        pending_subqueries = list(query.subqueries)  # 先保留 Query Agent 已规划但尚未执行的补充查询。
        executed_subqueries = [query.normalized_query]  # 首轮主查询视为已执行，防止演化服务重新生成等价表达。
        accumulated_papers: list[PaperRecord] = []  # 保存按身份优先级跨轮去重后的最终候选。
        paper_index: dict[str, int] = {}  # 将论文身份键映射到结果位置以保持首次排序顺序。
        discoveries = []  # 保存跨轮独立聚合的网页补充发现，永不并入论文集合。
        source_counts: dict[str, int] = {}  # 保存跨轮累计的来源成功数量。
        source_errors: dict[str, str] = {}  # 保存每个来源最新的安全错误摘要。
        selected_sources: list[PaperSource] = []  # 保存实际参与过学术检索的来源顺序。
        coverage_report: CoverageReport | None = None  # 保存每轮基于累计结果重新计算的覆盖判断。
        current_query = query  # 首轮直接执行用户编辑或 Query Agent 产出的完整查询计划。
        while state.current_round < max_rounds:  # 轮次上限是控制器最外层的强制保护条件。
            next_round = state.current_round + 1  # 在调用前计算即将完成的合法轮次编号。
            try:  # 允许协调器隔离来源级失败，但保护控制器免受未预期内部故障影响。
                round_result = await self._coordinator.recall(current_query)  # 执行一轮多源召回、融合、排序与核验。
            except Exception:  # 未预期异常不得形成无限循环或泄露内部细节。
                logger.exception("多轮搜索单轮协调失败：轮次=%d", next_round)  # 在受控日志记录堆栈而不记录完整用户查询。
                failed_state = state.model_copy(update={"status": "failed", "current_round": next_round, "stop_reason": "搜索执行出现内部错误", "errors": [*state.errors, "搜索执行出现内部错误"]})  # 返回可恢复的安全失败状态。
                return MultiRoundSearchResult(run_state=failed_state, query_intent=query, papers=accumulated_papers, discoveries=discoveries, source_counts=source_counts, source_errors=source_errors, coverage_report=coverage_report)  # 保留此前已获得的最佳结果。
            new_valid_count = _merge_round_papers(accumulated_papers, paper_index, round_result.papers)  # 仅将跨轮首次出现的论文计为本轮新增高质量结果。
            discoveries.extend(round_result.discoveries)  # 网页发现保持独立且允许跨轮累积。
            _accumulate_source_counts(source_counts, round_result.source_counts)  # 汇总所有轮次的来源成功数量。
            source_errors.update(round_result.source_errors)  # 让最终响应显示每个来源最后一次安全错误。
            _append_sources(selected_sources, round_result.route_plan.academic_sources)  # 保留所有实际选中的学术来源用于状态审计。
            coverage_report = self._coverage_gap_analyzer.analyze(  # 必须针对累计论文而非单轮结果判断是否真正补足缺口。
                query,
                accumulated_papers,
                new_valid_count=new_valid_count,
                source_counts=source_counts,
                unavailable_sources=tuple(source_errors),
                current_round=next_round,
                max_rounds=max_rounds,
                budget_exhausted=budget_exhausted,
                has_executable_query=True,
            )
            state = state.model_copy(  # 写入本轮可恢复状态、统计与当前累计候选。
                update={
                    "current_round": next_round,
                    "selected_sources": selected_sources,
                    "executed_subqueries": executed_subqueries,
                    "normalized_papers": accumulated_papers,
                    "candidate_ids": [paper.paper_id for paper in accumulated_papers],
                    "final_papers": accumulated_papers,
                    "api_call_count": state.api_call_count + len(round_result.route_plan.academic_sources) + len(round_result.route_plan.web_discovery_sources),
                    "token_usage": state.token_usage + round_result.llm_prompt_tokens + round_result.llm_completion_tokens,
                    "warnings": state.warnings,
                    "errors": [*state.errors, *round_result.source_errors.values()],
                    "degraded_sources": _degraded_sources(selected_sources, source_errors),
                    "coverage_report": coverage_report,
                }
            )
            logger.info("多轮搜索完成一轮：轮次=%d，新增高质量论文=%d，累计论文=%d，来源错误=%d，是否建议继续=%s", next_round, new_valid_count, len(accumulated_papers), len(source_errors), coverage_report.should_continue)  # 仅记录计数、布尔状态和轮次。
            if coverage_report.stop_reason is not None:  # 目标、预算、轮次或边际收益触发时立即停止。
                completed_state = state.model_copy(update={"status": "completed", "stop_reason": coverage_report.stop_reason})  # 保持当前最佳结果并写入可解释停止原因。
                return MultiRoundSearchResult(run_state=completed_state, query_intent=query, papers=accumulated_papers, discoveries=discoveries, source_counts=source_counts, source_errors=source_errors, coverage_report=coverage_report)  # 返回不额外调用来源的终态。
            evolution_result = self._query_evolution_service.evolve(query, coverage_report, executed_subqueries=executed_subqueries)  # 只针对当前缺口生成下一轮候选查询。
            pending_subqueries = _append_pending_subqueries(pending_subqueries, evolution_result.generated_subqueries, executed_subqueries)  # 追加通过演化去重的新查询并保留原规划顺序。
            if not pending_subqueries:  # 既无原计划也无演化得到的新查询时禁止重复调用首轮表达。
                coverage_report = self._coverage_gap_analyzer.analyze(query, accumulated_papers, new_valid_count=new_valid_count, source_counts=source_counts, unavailable_sources=tuple(source_errors), current_round=next_round, max_rounds=max_rounds, budget_exhausted=budget_exhausted, has_executable_query=False)  # 重算明确的“没有可执行新查询”停止原因。
                completed_state = state.model_copy(update={"status": "completed", "stop_reason": coverage_report.stop_reason, "coverage_report": coverage_report, "warnings": [*state.warnings, *evolution_result.warnings]})  # 保留演化跳过提示供 API 或 SSE 展示。
                return MultiRoundSearchResult(run_state=completed_state, query_intent=query, papers=accumulated_papers, discoveries=discoveries, source_counts=source_counts, source_errors=source_errors, coverage_report=coverage_report)  # 安全停止而不是回退到重复检索。
            next_subquery = pending_subqueries.pop(0)  # 按 Query Agent 原计划优先、再按缺口严重度执行下一条查询。
            executed_subqueries.append(next_subquery.query)  # 在发起下一轮前标记，确保异常恢复也不会重发同一查询。
            current_query = _query_for_subquery(query, next_subquery)  # 将子查询转换为适配器当前可消费的轮次专用 QueryIntent。
        completed_state = state.model_copy(update={"status": "completed", "stop_reason": "已达到最大搜索轮次"})  # 防御性处理循环自然结束的极端路径。
        return MultiRoundSearchResult(run_state=completed_state, query_intent=query, papers=accumulated_papers, discoveries=discoveries, source_counts=source_counts, source_errors=source_errors, coverage_report=coverage_report)  # 返回最后一轮已获得的最佳候选。


def _merge_round_papers(accumulated_papers: list[PaperRecord], paper_index: dict[str, int], papers: Sequence[PaperRecord]) -> int:
    """按项目既定身份优先级合并本轮论文，并返回真正新增的高质量数量。"""
    new_count = 0  # 只统计此前未出现身份键的候选。
    for paper in papers:  # 保持每轮已排序结果的出现顺序。
        identity = _paper_identity(paper)  # 使用 DOI、arXiv、PMID、来源平台 ID 和内部 ID 的既定优先级。
        existing_index = paper_index.get(identity)  # 查找是否已有等价论文。
        if existing_index is None:  # 首次发现的论文成为累计候选。
            paper_index[identity] = len(accumulated_papers)  # 保存其稳定位置供后续轮次去重。
            accumulated_papers.append(paper)  # 保留当前轮已完成排序和核验的记录。
            new_count += 1  # 计入本轮边际收益。
        else:  # 同一身份论文在新轮中可能包含更完整元数据或证据。
            accumulated_papers[existing_index] = paper  # 以最新已核验记录更新展示字段但不改变首次排序位置。
    return new_count  # 返回不使用重复论文虚增的新增数量。


def _paper_identity(paper: PaperRecord) -> str:
    """生成遵循 DOI、arXiv、PMID、来源平台 ID、内部 ID 顺序的跨轮身份键。"""
    if paper.doi:  # DOI 是跨来源最强的稳定论文身份。
        return f"doi:{paper.doi.strip().casefold()}"  # 规范化大小写与首尾空白避免同 DOI 重复。
    if paper.arxiv_id:  # 预印本身份在 DOI 缺失时优先于其他平台标识。
        return f"arxiv:{paper.arxiv_id.strip().casefold()}"  # 保持来源无关的 arXiv 去重键。
    if paper.pmid:  # 医学论文使用 PubMed 身份作为下一优先级。
        return f"pmid:{paper.pmid.strip().casefold()}"  # 规范化文本避免同一 PMID 重复。
    for source_name, external_id in (("openalex", paper.openalex_id), ("semantic_scholar", paper.semantic_scholar_id), ("dblp", paper.dblp_key)):  # 依次检查已有公开来源平台标识。
        if external_id:  # 仅为非空来源标识生成跨轮身份键。
            return f"{source_name}:{external_id.strip().casefold()}"  # 防止来源间相同字符串意外相撞。
    return f"paper:{paper.paper_id.strip().casefold()}"  # 最后回退到当前领域模型内部稳定标识。


def _accumulate_source_counts(total_counts: dict[str, int], round_counts: dict[str, int]) -> None:
    """原地累加每轮来源成功数量，避免覆盖此前轮次的审计数据。"""
    for source_name, count in round_counts.items():  # 逐来源合并本轮统计。
        total_counts[source_name] = total_counts.get(source_name, 0) + count  # 缺失来源从零开始累计。


def _append_sources(target: list[PaperSource], source_names: Sequence[PaperSource]) -> None:
    """按首次选择顺序合并实际参与检索的学术来源。"""
    for source_name in source_names:  # 保持路由器返回的来源顺序。
        if source_name not in target:  # 防止多轮重复来源污染运行状态。
            target.append(source_name)  # 保存首次被实际选择的来源。


def _degraded_sources(selected_sources: Sequence[PaperSource], source_errors: dict[str, str]) -> list[PaperSource]:
    """从来源错误中筛选仍属于论文来源的降级来源。"""
    return [source_name for source_name in selected_sources if source_name in source_errors]  # 排除 Tavily 等不属于 PaperSource 的补充网页来源。


def _append_pending_subqueries(pending: list[QuerySubquery], generated: Sequence[QuerySubquery], executed: Sequence[str]) -> list[QuerySubquery]:
    """合并原计划和新演化子查询，避免重复加入已执行文本。"""
    result = list(pending)  # 保留 Query Agent 原始计划的先后顺序。
    known = {item.strip().casefold() for item in [*executed, *(subquery.query for subquery in result)] if item.strip()}  # 使用文本规范键避免无意义大小写重复。
    for subquery in generated:  # 依次加入演化服务已通过相似度检查的查询。
        key = subquery.query.strip().casefold()  # 统一大小写和首尾空白。
        if key and key not in known:  # 仅保留尚未执行或尚未排队的子查询。
            result.append(subquery)  # 追加到原始计划之后以保持用户/模型计划优先。
            known.add(key)  # 标记避免同批重复。
    return result  # 返回下一轮可选择的唯一子查询队列。


def _query_for_subquery(query: QueryIntent, subquery: QuerySubquery) -> QueryIntent:
    """将单条子查询转换为当前来源适配器可直接消费的轮次专用查询意图。"""
    return query.model_copy(  # 保留年份、作者、排除和硬约束等所有用户条件。
        update={
            "normalized_query": subquery.query,  # 让来源适配器在缺少结构化词时可回退到当前子查询。
            "research_topics": [subquery.query],  # 当前适配器按结构化词构建请求，因此明确以子查询替换主主题。
            "methods": [],  # 避免将首轮方法词重复拼入已完整的子查询文本。
            "tasks": [],  # 避免将首轮任务词重复拼入已完整的子查询文本。
            "datasets": [],  # 避免将首轮数据集词重复拼入已完整的子查询文本。
            "subqueries": [],  # 当前轮只执行选中的一条，避免将待执行队列误视为本轮上下文。
        }
    )
