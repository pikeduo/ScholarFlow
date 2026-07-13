"""提供 LangGraph 多轮搜索工作流依赖的应用服务边界与领域辅助函数。"""

from collections.abc import Sequence  # 接收不可变的来源、查询和论文候选序列。
from typing import Protocol  # 定义可离线替换的单轮召回服务边界。

from backend.app.core.logging import logger  # 记录不含完整查询文本的持久化与事件发布异常。
from backend.app.models.coverage import CoverageReport  # 传递累计候选的覆盖判断与停止原因。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 保持控制器对外稳定结果契约。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 消费单轮召回、排序和核验结果。
from backend.app.models.paper import PaperRecord, PaperSource  # 保存跨轮去重论文与来源状态。
from backend.app.models.query_evolution import QueryEvolutionResult  # 返回查询演化服务的稳定结果。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 维护主查询与补充子查询。
from backend.app.models.search_run import SearchRunState  # 保存可恢复的运行状态快照。
from backend.app.models.search_event import SearchProgressEvent  # 发布不含论文详情的轻量进度事件。
from backend.app.services.coverage_analysis import CoverageGapAnalyzer  # 提供纯本地覆盖分析和停止判断。
from backend.app.services.query_evolution import QueryEvolutionService  # 提供受硬约束保护的查询演化。
from backend.app.services.search_events import SearchRunEventPublisher  # 解耦 SSE 或 Redis 进度发布实现。
from backend.app.services.search_run_store import SearchRunStateStore  # 持久化每个工作流节点后的轻量快照。


class SingleRoundRecallCoordinator(Protocol):
    """定义多轮工作流所需的单轮召回服务能力。"""

    async def recall(self, query: QueryIntent) -> MultiSourceRecallResult:
        """执行一轮已完成来源路由、融合、排序和核验的搜索。"""
        ...  # 协议不承载适配器 HTTP、鉴权或供应商字段。


class MultiRoundSearchController:
    """装配 LangGraph 工作流所需服务，维持原有公共搜索调用契约。"""

    def __init__(self, coordinator: SingleRoundRecallCoordinator, coverage_gap_analyzer: CoverageGapAnalyzer | None = None, query_evolution_service: QueryEvolutionService | None = None, state_store: SearchRunStateStore | None = None, standard_max_rounds: int = 2, deep_max_rounds: int = 3) -> None:
        """保存可替换协作者和标准、深度模式的硬轮次上限。

        参数：
            coordinator：执行单轮来源召回和分层排序的服务。
            coverage_gap_analyzer：分析累计候选覆盖并给出停止决策的服务。
            query_evolution_service：根据缺口生成去重补充查询的服务。
            state_store：可选运行状态存储，节点边界会尽力保存轻量快照。
            standard_max_rounds：标准模式允许的最大轮次。
            deep_max_rounds：深度模式允许的最大轮次。
        异常：
            ValueError：轮次上限不在一到三之间或深度模式小于标准模式时抛出。
        """
        if not 1 <= standard_max_rounds <= 3:  # 标准模式必须保留首轮且符合 SearchRunState 上限。
            raise ValueError("standard_max_rounds 必须位于 [1, 3] 区间")  # 防止构造无法收敛的工作流。
        if not standard_max_rounds <= deep_max_rounds <= 3:  # 深度模式不得短于标准模式且不超过状态契约。
            raise ValueError("deep_max_rounds 必须位于 [standard_max_rounds, 3] 区间")  # 保持成本策略可解释。
        self._coordinator = coordinator  # 保存不绑定适配器、模型或 API 的单轮服务。
        self._coverage_gap_analyzer = coverage_gap_analyzer or CoverageGapAnalyzer()  # 默认使用纯本地覆盖分析。
        self._query_evolution_service = query_evolution_service or QueryEvolutionService()  # 默认使用不调用外部服务的确定性演化。
        self._state_store = state_store  # 未装配持久化时允许纯内存单元测试。
        self._standard_max_rounds = standard_max_rounds  # 保存标准模式硬上限。
        self._deep_max_rounds = deep_max_rounds  # 保存深度模式硬上限。

    async def run(self, query: QueryIntent, *, budget_exhausted: bool = False, event_publisher: SearchRunEventPublisher | None = None) -> MultiRoundSearchResult:
        """通过 LangGraph 条件图执行多轮搜索并返回稳定结果。"""
        from backend.app.agents.search_workflow import MultiRoundSearchWorkflow  # 延迟导入避免 Agent 与服务协议形成模块循环。

        return await MultiRoundSearchWorkflow(self).run(query, budget_exhausted=budget_exhausted, event_publisher=event_publisher)  # 所有生产调用统一走条件节点图。

    def max_rounds_for(self, query: QueryIntent) -> int:
        """按搜索模式返回已配置且已校验的硬轮次上限。"""
        return self._deep_max_rounds if query.search_mode == "deep" else self._standard_max_rounds  # 将模式策略集中在服务层。

    async def recall_once(self, query: QueryIntent) -> MultiSourceRecallResult:
        """委托已装配协调器执行一轮多源召回、排序和核验。"""
        return await self._coordinator.recall(query)  # 保持来源调用、鉴权和排序细节位于服务与适配层。

    def analyze_coverage(self, query: QueryIntent, papers: list[PaperRecord], *, new_valid_count: int, source_counts: dict[str, int], unavailable_sources: tuple[str, ...], current_round: int, max_rounds: int, budget_exhausted: bool, has_executable_query: bool) -> CoverageReport:
        """委托纯本地覆盖服务生成继续或停止决策。"""
        return self._coverage_gap_analyzer.analyze(query, papers, new_valid_count=new_valid_count, source_counts=source_counts, unavailable_sources=unavailable_sources, current_round=current_round, max_rounds=max_rounds, budget_exhausted=budget_exhausted, has_executable_query=has_executable_query)  # 节点不直接依赖具体分析实现。

    def evolve_query(self, query: QueryIntent, coverage_report: CoverageReport, *, executed_subqueries: list[str]) -> QueryEvolutionResult:
        """委托查询演化服务生成遵循硬约束的补充子查询。"""
        return self._query_evolution_service.evolve(query, coverage_report, executed_subqueries=executed_subqueries)  # 保持演化去重规则位于可单测服务层。

    def persist_state(self, state: SearchRunState) -> None:
        """尽力保存轻量运行快照，存储失败不影响搜索控制流。"""
        if self._state_store is None:  # 未装配存储时保持离线测试和纯内存运行可用。
            return  # 不为可选观测能力引入额外基础设施。
        try:  # 存储失败不得触发来源重复调用。
            self._state_store.save(state)  # 存储实现负责轻量化并原子提交。
        except RuntimeError:  # 存储适配层已隐藏 SQL、路径和查询正文。
            logger.exception("多轮搜索状态持久化降级：运行=%s，轮次=%d", state.run_id, state.current_round)  # 仅记录安全标识与轮次。

    def publish_event(self, publisher: SearchRunEventPublisher | None, state: SearchRunState, event_type: str, node: str, message: str, *, current_round: int | None = None, progress: float | None = None, metrics: dict[str, int | float | str | bool] | None = None) -> None:
        """发布安全进度事件，通道故障不影响检索结果与停止条件。"""
        if publisher is None:  # 普通 REST 请求不创建或发送进度事件。
            return  # 保持无 SSE 调用路径轻量。
        try:  # 慢客户端或发布器异常不得阻塞工作流。
            publisher.publish(SearchProgressEvent(run_id=state.run_id, event_type=event_type, node=node, current_round=state.current_round if current_round is None else current_round, progress=progress, message=message, metrics=metrics or {}))  # 只发布状态、数量和安全停止原因。
        except RuntimeError:  # 发布器可用稳定异常表达自身不可用。
            logger.exception("多轮搜索进度事件发布降级：运行=%s，轮次=%d", state.run_id, state.current_round)  # 不记录完整查询或论文内容。


def _merge_round_papers(accumulated_papers: list[PaperRecord], paper_index: dict[str, int], papers: Sequence[PaperRecord]) -> int:
    """按项目既定身份优先级合并本轮论文，并返回真正新增的高质量数量。"""
    new_count = 0  # 只统计此前未出现身份键的候选。
    for paper in papers:  # 保持每轮已排序结果的出现顺序。
        identity = _paper_identity(paper)  # 使用 DOI、arXiv、PMID、来源平台 ID 和内部 ID 的既定优先级。
        existing_index = paper_index.get(identity)  # 查找是否已有等价论文。
        if existing_index is None:  # 首次发现论文成为累计候选。
            paper_index[identity] = len(accumulated_papers)  # 保存稳定位置供后续轮次去重。
            accumulated_papers.append(paper)  # 保留已完成排序和核验的记录。
            new_count += 1  # 计入本轮边际收益。
        else:  # 同一身份论文可能带来更完整元数据或证据。
            accumulated_papers[existing_index] = paper  # 更新展示字段但不改变首次排序位置。
    return new_count  # 返回不使用重复论文虚增的新增数量。


def _paper_identity(paper: PaperRecord) -> str:
    """生成 DOI、arXiv、PMID、来源平台 ID、内部 ID 顺序的跨轮身份键。"""
    if paper.doi:  # DOI 是跨来源最强的稳定身份。
        return f"doi:{paper.doi.strip().casefold()}"  # 规范化大小写和空白避免重复。
    if paper.arxiv_id:  # 预印本身份在 DOI 缺失时优先。
        return f"arxiv:{paper.arxiv_id.strip().casefold()}"  # 保持来源无关的 arXiv 去重键。
    if paper.pmid:  # 医学论文使用 PubMed 身份作为下一优先级。
        return f"pmid:{paper.pmid.strip().casefold()}"  # 规范化文本避免相同 PMID 重复。
    for source_name, external_id in (("openalex", paper.openalex_id), ("semantic_scholar", paper.semantic_scholar_id), ("dblp", paper.dblp_key)):  # 依次检查公开来源平台标识。
        if external_id:  # 仅为非空标识生成跨轮键。
            return f"{source_name}:{external_id.strip().casefold()}"  # 防止不同来源相同文本意外相撞。
    return f"paper:{paper.paper_id.strip().casefold()}"  # 最后回退到领域内部稳定标识。


def _accumulate_source_counts(total_counts: dict[str, int], round_counts: dict[str, int]) -> None:
    """原地累加每轮来源成功数量，避免覆盖此前轮次审计数据。"""
    for source_name, count in round_counts.items():  # 逐来源合并本轮统计。
        total_counts[source_name] = total_counts.get(source_name, 0) + count  # 缺失来源从零开始累计。


def _append_sources(target: list[PaperSource], source_names: Sequence[PaperSource]) -> None:
    """按首次选择顺序合并实际参与检索的学术来源。"""
    for source_name in source_names:  # 保持路由器返回的来源顺序。
        if source_name not in target:  # 防止多轮重复来源污染运行状态。
            target.append(source_name)  # 保存首次被实际选择的来源。


def _degraded_sources(selected_sources: Sequence[PaperSource], source_errors: dict[str, str]) -> list[PaperSource]:
    """从来源错误中筛选仍属于论文来源的降级来源。"""
    return [source_name for source_name in selected_sources if source_name in source_errors]  # 排除 Tavily 等不属于 PaperSource 的补充来源。


def _append_pending_subqueries(pending: list[QuerySubquery], generated: Sequence[QuerySubquery], executed: Sequence[str]) -> list[QuerySubquery]:
    """合并原计划和演化子查询，避免重复加入已执行文本。"""
    result = list(pending)  # 保留 Query Agent 原计划的先后顺序。
    known = {item.strip().casefold() for item in [*executed, *(subquery.query for subquery in result)] if item.strip()}  # 构建大小写无关的已知查询集合。
    for subquery in generated:  # 依次加入演化服务已通过相似度检查的查询。
        key = subquery.query.strip().casefold()  # 统一大小写和首尾空白。
        if key and key not in known:  # 仅保留尚未执行或尚未排队的文本。
            result.append(subquery)  # 追加到原始计划之后以保持用户或模型计划优先。
            known.add(key)  # 标记避免同批重复。
    return result  # 返回下一轮可选择的唯一子查询队列。


def _query_for_subquery(query: QueryIntent, subquery: QuerySubquery) -> QueryIntent:
    """将单条子查询转换为来源适配器可消费的轮次专用意图。"""
    return query.model_copy(update={"normalized_query": subquery.query, "research_topics": [subquery.query], "methods": [], "tasks": [], "datasets": [], "subqueries": []})  # 保留硬约束并避免重复拼接首轮结构词。
