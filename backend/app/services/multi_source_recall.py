"""协调排序前候选生成、分层排序、约束核验与覆盖缺口分析。"""

from backend.app.core.logging import logger  # 记录不包含查询正文和论文文本的阶段统计。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 构造现有公共多源搜索响应。
from backend.app.models.query_intent import QueryIntent  # 接收已完成查询规划的统一意图。
from backend.app.services.candidate_generation import CandidateGenerationService  # 复用规则过滤后、BGE-M3 前的共享候选边界。
from backend.app.services.coverage_analysis import CoverageGapAnalyzer  # 在最终核验后识别下一轮检索需要补足的缺口。
from backend.app.services.cross_encoder_ranking import CrossEncoderReranker  # 在 BGE-M3 粗排后执行 Cross Encoder 精细重排。
from backend.app.services.llm_ranking import LlmPaperReranker  # 在 Cross Encoder 后执行约束核验、理由生成与最终截断。
from backend.app.services.semantic_ranking import SemanticRanker  # 在规则过滤后执行 BGE-M3 粗排和候选截断。


class MultiSourceRecallCoordinator:
    """在共享排序前候选上执行 BGE-M3、Cross Encoder、LLM 与覆盖分析。

    参数：
        candidate_generation_service：执行来源调用、身份融合、RRF 与规则过滤的内部服务。
        semantic_ranker：可替换的 BGE-M3 语义粗排服务。
        cross_encoder_reranker：可替换的 Cross Encoder 精细重排服务。
        llm_reranker：可替换的 LLM 约束核验和最终精排服务。
        coverage_gap_analyzer：可替换的本地覆盖缺口分析服务。
    """

    def __init__(
        self,
        candidate_generation_service: CandidateGenerationService,
        semantic_ranker: SemanticRanker | None = None,
        cross_encoder_reranker: CrossEncoderReranker | None = None,
        llm_reranker: LlmPaperReranker | None = None,
        coverage_gap_analyzer: CoverageGapAnalyzer | None = None,
    ) -> None:
        """保存共享候选生成边界和可替换排序服务，不在构造阶段执行 I/O。"""
        self._candidate_generation_service = candidate_generation_service  # 保存可独立调用和测试的排序前候选服务。
        self._semantic_ranker = semantic_ranker or SemanticRanker()  # 默认在规则过滤后执行可降级的 BGE-M3 粗排。
        self._cross_encoder_reranker = cross_encoder_reranker or CrossEncoderReranker()  # 默认在 BGE-M3 后执行可降级精排。
        self._llm_reranker = llm_reranker or LlmPaperReranker()  # 默认生成证据化最终结果并允许测试替换。
        self._coverage_gap_analyzer = coverage_gap_analyzer or CoverageGapAnalyzer()  # 默认生成无副作用覆盖报告。

    async def recall(self, query: QueryIntent) -> MultiSourceRecallResult:
        """生成排序前候选并保持既有分层排序和公共响应行为。

        参数：
            query：已由 Query Agent 或直接意图入口校验的完整检索意图。
        返回：
            MultiSourceRecallResult：最终论文、独立网页发现、来源统计和排序降级信息。
        """
        candidate_result = await self._candidate_generation_service.generate(query)  # 仅执行到规则过滤后的共享候选边界。
        ranking_result = self._semantic_ranker.rank(candidate_result.papers, query, enabled=query.enable_semantic_ranking, disabled_reason="用户未启用 BGE-M3 语义粗排，已按 RRF 排序")  # 保持现有 BGE-M3 开关和降级摘要。
        cross_encoder_result = self._cross_encoder_reranker.rerank(ranking_result.papers, query, enabled=query.enable_cross_encoder_ranking, disabled_reason="用户未启用 Cross Encoder 重排，已沿用 BGE-M3 或 RRF 排序")  # 保持现有 Cross Encoder 开关和降级摘要。
        llm_result = await self._llm_reranker.rerank(cross_encoder_result.papers, query)  # 保持现有 LLM 约束核验、理由生成和截断行为。
        source_counts = candidate_result.source_counts  # 合并两类来源统计以兼容现有公共响应和覆盖分析输入。
        source_errors = candidate_result.source_errors  # 合并两类安全降级摘要以兼容现有公共响应。
        coverage_report = self._coverage_gap_analyzer.analyze(query, llm_result.papers, new_valid_count=len(llm_result.papers), source_counts=source_counts, unavailable_sources=tuple(source_errors))  # 使用最终核验结果保持原覆盖分析语义。
        logger.info(  # 保持完整流水线计数可观测且不输出论文文本。
            "多源召回完成：规范化论文=%d，融合论文=%d，过滤=%d，语义截断=%d，交叉编码截断=%d，LLM淘汰=%d，LLM截断=%d，最终结果=%d，网页发现=%d，来源错误=%d",
            candidate_result.normalized_candidate_count,
            candidate_result.deduplicated_candidate_count,
            candidate_result.filtered_candidate_count,
            ranking_result.truncated_count,
            cross_encoder_result.truncated_count,
            llm_result.rejected_count,
            llm_result.truncated_count,
            len(llm_result.papers),
            len(candidate_result.discoveries),
            len(source_errors),
        )
        return MultiSourceRecallResult(  # 映射为现有公共响应且不增加或删除 API 字段。
            route_plan=candidate_result.route_plan,  # 保留本轮真实来源选择计划。
            query_intent=query,  # 回显实际执行的结构化查询。
            papers=llm_result.papers,  # 返回完成全部分层排序和约束核验的最终论文。
            discoveries=candidate_result.discoveries,  # 返回不可合并的补充网页发现项。
            source_counts=source_counts,  # 保持现有混合来源数量响应字段。
            source_errors=source_errors,  # 保持现有来源级安全错误字段。
            cache_hit_count=candidate_result.cache_hit_count,  # 返回本轮来源响应缓存命中数。
            raw_paper_count=candidate_result.normalized_candidate_count,  # 兼容旧字段名，值仍为适配器已映射并进入融合的论文数。
            merged_paper_count=candidate_result.merged_candidate_count,  # 返回身份融合合并的重复记录数量。
            filtered_paper_count=candidate_result.filtered_candidate_count,  # 返回确定性规则移除数量。
            filter_reason_counts=candidate_result.filter_reason_counts,  # 返回按首个失败规则汇总的过滤统计。
            semantic_truncated_count=ranking_result.truncated_count,  # 返回 BGE-M3 阶段截断数量。
            semantic_ranking_error=ranking_result.ranking_error,  # 返回 BGE-M3 跳过或不可用摘要。
            cross_encoder_truncated_count=cross_encoder_result.truncated_count,  # 返回 Cross Encoder 阶段截断数量。
            cross_encoder_ranking_error=cross_encoder_result.ranking_error,  # 返回 Cross Encoder 跳过或不可用摘要。
            llm_truncated_count=llm_result.truncated_count,  # 返回核验通过但超出最终数量的候选数。
            llm_rejected_count=llm_result.rejected_count,  # 返回 LLM 明确判定不满足约束的候选数。
            llm_ranking_error=llm_result.ranking_error,  # 返回 LLM 不可用时的安全降级摘要。
            llm_model_name=llm_result.model_name,  # 返回实际或配置模型名供成本审计。
            llm_prompt_tokens=llm_result.prompt_tokens,  # 返回 LLM 精排输入 Token。
            llm_completion_tokens=llm_result.completion_tokens,  # 返回 LLM 精排输出 Token。
            llm_estimated_cost_cny=llm_result.estimated_cost_cny,  # 返回依据供应商 usage 冻结的费用估算。
            llm_peak_pricing_applied=llm_result.peak_pricing_applied,  # 返回是否命中工作时间费率。
            work_family_count=candidate_result.work_family_count,  # 返回规则过滤后候选中的唯一版本族数量。
            coverage_report=coverage_report,  # 返回不触发额外调用的覆盖缺口报告。
        )
