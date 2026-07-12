"""以可解释的本地规则分析最终检索结果的覆盖缺口。"""

from collections.abc import Mapping, Sequence  # 接收来源统计与不可变论文候选序列。

from backend.app.models.coverage import CoverageGap, CoverageReport  # 构造供 API 与多轮控制器共享的覆盖报告。
from backend.app.models.paper import PaperRecord  # 分析已经完成排序和核验的最终论文记录。
from backend.app.models.query_intent import QueryIntent  # 读取不可放宽的约束和目标数量。


class CoverageGapAnalyzer:
    """分析当前候选对 QueryIntent 的显式覆盖，并给出后续一轮的继续建议。

    本服务只检查公开元数据与服务端已验证的约束状态；不调用 LLM、外部 API 或模型，
    因而可安全地用于每轮检索结束后的停止判断。
    """

    def __init__(self, minimum_new_valid_count: int = 1) -> None:
        """保存判断非首轮边际收益是否足够的最小新增高质量论文数。

        参数：
            minimum_new_valid_count：非首轮继续搜索所需的最小新增高质量论文数。
        异常：
            ValueError：阈值不是正整数时抛出。
        """
        if minimum_new_valid_count < 1:  # 零阈值会让无新增结果的工作流无法收敛。
            raise ValueError("minimum_new_valid_count 必须大于零")  # 在装配阶段提供稳定配置错误。
        self._minimum_new_valid_count = minimum_new_valid_count  # 保存独立于具体来源和模型的停止阈值。

    def analyze(
        self,
        query: QueryIntent,
        papers: Sequence[PaperRecord],
        *,
        new_valid_count: int,
        source_counts: Mapping[str, int],
        unavailable_sources: Sequence[str] = (),
        current_round: int = 1,
        max_rounds: int = 3,
        budget_exhausted: bool = False,
        has_executable_query: bool = True,
    ) -> CoverageReport:
        """生成当前轮次的覆盖报告与不触发副作用的继续建议。

        参数：
            query：当前轮实际执行的结构化查询意图。
            papers：已完成 LLM 约束核验的最终候选。
            new_valid_count：相对上一轮新增的高质量论文数量。
            source_counts：本轮各已选来源成功返回的条目数。
            unavailable_sources：本轮不可用或失败的已选学术来源名称。
            current_round：当前已完成的检索轮次，从一开始计数。
            max_rounds：控制器允许的最大检索轮次。
            budget_exhausted：API、Token、费用或耗时预算是否已触顶。
            has_executable_query：后续 Query Evolution 是否仍有未执行的可行查询。
        返回：
            CoverageReport：包含缺口、边际收益和继续或停止判断的稳定结果。
        异常：
            ValueError：轮次、最大轮次或新增数量无效时抛出。
        """
        if new_valid_count < 0:  # 新增数量不应以负数表达候选淘汰。
            raise ValueError("new_valid_count 不能小于零")  # 让调用方显式传递本轮新增而非净损失。
        if current_round < 1:  # 首轮之前无法判断边际收益或停止原因。
            raise ValueError("current_round 必须大于零")  # 保持工作流轮次语义一致。
        if max_rounds < current_round:  # 超出最大轮次会使继续判断失去意义。
            raise ValueError("max_rounds 不能小于 current_round")  # 在控制器接入前先守住公共服务边界。
        high_relevance_papers = [paper for paper in papers if _is_high_relevance(paper, query)]  # 仅将已满足关键约束的结果计入目标完成度。
        partial_relevance_count = sum(1 for paper in papers if paper.constraint_status == "uncertain")  # 单独统计证据不足但仍可供用户审阅的候选。
        gaps = self._build_gaps(query, papers, high_relevance_papers, source_counts, unavailable_sources)  # 根据明确约束和来源统计构造可演化缺口。
        marginal_gain = min(1.0, new_valid_count / query.target_paper_count)  # 以目标数量归一化本轮新增价值。
        stop_reason = self._stop_reason(  # 按保护性顺序判断是否禁止继续检索。
            query=query,
            high_relevance_count=len(high_relevance_papers),
            gaps=gaps,
            new_valid_count=new_valid_count,
            unavailable_sources=unavailable_sources,
            current_round=current_round,
            max_rounds=max_rounds,
            budget_exhausted=budget_exhausted,
            has_executable_query=has_executable_query,
        )
        return CoverageReport(  # 返回纯数据结果，具体工作流是否进入下一轮由后续控制器负责。
            target_count=query.target_paper_count,
            high_relevance_count=len(high_relevance_papers),
            partial_relevance_count=partial_relevance_count,
            gaps=gaps,
            new_valid_count=new_valid_count,
            marginal_gain=marginal_gain,
            should_continue=stop_reason is None and bool(gaps),
            stop_reason=stop_reason,
        )

    def _build_gaps(
        self,
        query: QueryIntent,
        papers: Sequence[PaperRecord],
        high_relevance_papers: Sequence[PaperRecord],
        source_counts: Mapping[str, int],
        unavailable_sources: Sequence[str],
    ) -> list[CoverageGap]:
        """按硬约束、领域条件、来源和数量构造并排序当前覆盖缺口。"""
        gaps: list[CoverageGap] = []  # 收集每个可由后续查询演化单独处理的不足。
        for constraint in query.must_include:  # 硬约束缺失拥有最高优先级且不能被自动放宽。
            match_count = _count_explicit_matches(high_relevance_papers, constraint)  # 只统计已经通过核验的候选。
            if match_count == 0:  # 没有公开元数据证据时明确记录关键缺口。
                gaps.append(_gap("must_include", constraint, 1.0, match_count))  # 建议下一轮聚焦原始硬约束文本。
        for dataset in query.datasets:  # 数据集约束可由标题、摘要或关键词提供显式覆盖证据。
            match_count = _count_explicit_matches(high_relevance_papers, dataset)  # 统计最终高相关候选中的显式命中。
            if match_count == 0:  # 数据集未确认时应交给 Query Evolution 生成补充检索式。
                gaps.append(_gap("dataset", dataset, 0.9, match_count))  # 保持低于硬约束但高于数量不足的优先级。
        for method in query.methods:  # 方法约束同样只使用公开可定位的元数据文本。
            match_count = _count_explicit_matches(high_relevance_papers, method)  # 统计已核验候选对方法条件的覆盖。
            if match_count == 0:  # 方法未确认不能由引用数或 RRF 分数替代。
                gaps.append(_gap("method", method, 0.8, match_count))  # 提供可直接拼入下一轮检索的聚焦文本。
        if query.year_range is not None:  # 仅在用户明确指定年份范围时检查该硬条件。
            start_year, end_year = query.year_range  # 解构经过 QueryIntent 校验的闭区间。
            match_count = sum(1 for paper in high_relevance_papers if paper.year is not None and start_year <= paper.year <= end_year)  # 只统计年份信息明确且在范围内的论文。
            if match_count == 0:  # 没有目标年份内高相关论文时提示补充年份专用查询。
                gaps.append(_gap("year_range", f"{start_year}-{end_year}", 0.85, match_count))  # 保存可展示的闭区间文本而不修改原条件。
        for source_name, result_count in source_counts.items():  # 检查本轮实际选中的来源是否提供了可用覆盖。
            if result_count == 0:  # 无结果或来源故障均说明该来源未贡献本轮覆盖。
                severity = 0.7 if source_name in unavailable_sources else 0.45  # 失败来源比正常空结果更值得优先修复或绕开。
                gaps.append(_gap("source", source_name, severity, 0))  # 供后续路由或查询演化决定是否切换来源。
        if len(high_relevance_papers) < query.target_paper_count:  # 目标数量不足必须显式报告而不是用低相关论文填充。
            severity = (query.target_paper_count - len(high_relevance_papers)) / query.target_paper_count  # 按缺少比例表达结果数量风险。
            gaps.append(_gap("result_count", "高相关论文数量", severity, len(high_relevance_papers)))  # 保留当前高相关数量供前端解释。
        return sorted(gaps, key=lambda gap: (-gap.severity, gap.gap_type, gap.constraint))  # 使用稳定排序便于测试、缓存和前端展示。

    def _stop_reason(
        self,
        *,
        query: QueryIntent,
        high_relevance_count: int,
        gaps: Sequence[CoverageGap],
        new_valid_count: int,
        unavailable_sources: Sequence[str],
        current_round: int,
        max_rounds: int,
        budget_exhausted: bool,
        has_executable_query: bool,
    ) -> str | None:
        """按目标完成、预算、轮次、查询、来源和边际收益顺序返回停止原因。"""
        has_constraint_gap = any(gap.gap_type in {"must_include", "dataset", "method", "year_range"} for gap in gaps)  # 区分数量不足和关键约束未覆盖。
        if high_relevance_count >= query.target_paper_count and not has_constraint_gap:  # 目标数量与明确关键约束均已满足时无需再增加调用。
            return "已获得目标数量的高相关论文且关键约束已覆盖"  # 返回可直接展示的正常完成原因。
        if budget_exhausted:  # 预算触顶时必须保留现有最佳结果并停止扩展。
            return "搜索预算已达到上限"  # 不暴露具体余额或供应商成本细节。
        if current_round >= max_rounds:  # 最大轮次是防止无限检索的硬保护条件。
            return "已达到最大搜索轮次"  # 由后续控制器将此原因写入 SearchRunState。
        if not has_executable_query:  # 没有新查询时重复调用同一来源只会放大成本。
            return "没有可执行的新查询"  # 明确说明停止不是因为生成低相关结果。
        if unavailable_sources and len(unavailable_sources) >= len(source_names_from_gaps(gaps)):  # 所有本轮来源都不可用时继续没有实际价值。
            return "可用学术来源不足"  # 避免将单来源故障错误描述为用户查询无结果。
        if current_round > 1 and new_valid_count < self._minimum_new_valid_count:  # 非首轮无新增高质量论文说明边际收益已经不足。
            return "连续轮次新增高质量论文不足"  # 保护 API、Token 和模型成本预算。
        return None  # 存在缺口且尚未触发停止条件时建议控制器进入下一轮。


def _is_high_relevance(paper: PaperRecord, query: QueryIntent) -> bool:
    """判断论文能否计入目标完成度，同时兼容无硬约束和 LLM 降级路径。"""
    if paper.constraint_status == "satisfied":  # 已有可信证据的硬约束满足结果始终属于高相关集合。
        return True  # 不再依赖模型分数避免重复阈值判断。
    if query.must_include or query.datasets or query.methods or query.year_range is not None:  # 存在明确关键条件时，证据不足的论文不能计入完成度。
        return False  # 防止用 uncertain 候选虚假宣称约束已覆盖。
    return paper.llm_relevance_score is None or paper.llm_relevance_score >= 0.5  # 无硬约束时允许 LLM 降级候选按现有最终排序计入。


def _count_explicit_matches(papers: Sequence[PaperRecord], constraint: str) -> int:
    """统计约束文本在最终论文公开元数据中可直接定位的覆盖数量。"""
    normalized_constraint = constraint.strip().casefold()  # 统一空白和大小写以进行稳定文本比较。
    if not normalized_constraint:  # 空约束不应被解释为任意论文均已覆盖。
        return 0  # 保持缺口报告保守且不虚构匹配。
    return sum(1 for paper in papers if normalized_constraint in _paper_metadata_text(paper))  # 只计算可在公开字段中定位的命中。


def _paper_metadata_text(paper: PaperRecord) -> str:
    """拼接允许用于覆盖判断的公开论文元数据字段。"""
    return "\n".join(  # 用换行避免相邻字段拼接形成虚假的跨字段关键词。
        part
        for part in (paper.title, paper.abstract, " ".join(paper.keywords), paper.venue or "", paper.paper_type or "")
        if part
    ).casefold()  # 覆盖判断不区分大小写但不修改原始展示文本。


def _gap(gap_type: str, constraint: str, severity: float, current_match_count: int) -> CoverageGap:
    """构造保持原约束文本的单个覆盖缺口。"""
    return CoverageGap(gap_type=gap_type, constraint=constraint, severity=severity, current_match_count=current_match_count, recommended_query_focus=constraint)  # 不自动放宽或改写用户条件。


def source_names_from_gaps(gaps: Sequence[CoverageGap]) -> set[str]:
    """从来源缺口中提取本轮已选学术来源名称以判断整体可用性。"""
    return {gap.constraint for gap in gaps if gap.gap_type == "source"}  # 仅返回来源级缺口，避免误将条件文本视为来源。
