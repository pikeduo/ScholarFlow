"""实现基于 LLM 的约束核验、推荐理由生成与最终候选截断。"""

from backend.app.adapters.deepseek_llm import DeepSeekPaperAssessmentClient, LlmAssessmentError, PaperAssessmentClient  # 依赖可替换的 LLM 适配器协议。
from backend.app.core.logging import logger  # 记录精排数量、Token 与安全降级状态。
from backend.app.core.config import settings  # 读取最终最低相关度配置。
from backend.app.models.llm_ranking import ConstraintMatchStatus, LlmPaperAssessment, LlmRankingResult  # 构造最终精排结果并校验状态。
from backend.app.models.paper import PaperRecord  # 接收和更新 Cross Encoder 候选论文。
from backend.app.models.query_intent import QueryIntent  # 接收目标结果数量和结构化约束。


DEFAULT_FINAL_RESULT_LIMIT = 20  # 标准搜索最终最多返回二十篇论文。
HIGH_RELEVANCE_SCORE = 0.60  # LLM 分数达到 0.60 才可展示为高相关，较低保留分数统一归入部分相关。


class LlmPaperReranker:
    """核验 Cross Encoder 候选、绑定公开证据并生成最终论文结果。"""

    def __init__(self, client: PaperAssessmentClient | None = None, result_limit: int = DEFAULT_FINAL_RESULT_LIMIT, model_name: str = "deepseek-v4-flash", minimum_relevance_score: float = settings.llm_minimum_relevance_score, batch_size: int = settings.deepseek_llm_batch_size, enabled: bool = settings.llm_ranking_enabled) -> None:
        """保存可替换客户端、最终上限和降级时使用的模型标识。"""
        if result_limit < 1:  # 无效上限会使正常候选被全部丢弃。
            raise ValueError("result_limit 必须大于零")  # 在服务装配阶段尽早暴露配置错误。
        if not 5 <= batch_size <= 10:  # 论文核验必须遵守阶段规划定义的五至十篇批量边界。
            raise ValueError("batch_size 必须在 5 到 10 之间")  # 阻止大批量请求再次导致模型响应超时。
        self._client = client or DeepSeekPaperAssessmentClient()  # 默认使用 DeepSeek，测试可注入完全离线替身。
        self._result_limit = result_limit  # 保存系统级最终结果硬上限。
        self._model_name = model_name  # 保存 API 不可用时仍可观测的配置模型名。
        self._minimum_relevance_score = minimum_relevance_score  # 保存最终结果最低相关度，零分候选不再透传。
        self._batch_size = batch_size  # 保存每次 DeepSeek 核验允许发送的最大论文数。
        self._enabled = enabled  # 保存是否应跳过 DeepSeek 精排以缩短快速检索路径。

    async def rerank(self, papers: list[PaperRecord], query: QueryIntent) -> LlmRankingResult:
        """调用 LLM 核验候选并返回最多目标数量的证据化结果。"""
        target_count = min(query.target_paper_count, self._result_limit)  # 同时尊重用户目标与系统成本上限。
        if not papers:  # 空候选不应调用外部 LLM。
            return LlmRankingResult(papers=[], input_count=0, model_name=self._model_name)  # 返回稳定空结果。
        if not self._enabled:  # 快速路径不发送论文元数据或查询给 DeepSeek。
            return self._disabled_result(papers, target_count)  # 沿用上游排序并保留主动跳过说明。
        paper_by_id = {paper.paper_id: paper for paper in papers}  # 使用稳定 ID 防止模型改变数组顺序或注入候选。
        assessment_by_id: dict[str, LlmPaperAssessment] = {}  # 去除未知 ID 和重复模型输出。
        prompt_tokens = 0  # 累计每个成功核验批次的输入 Token 用量。
        completion_tokens = 0  # 累计每个成功核验批次的输出 Token 用量。
        model_name = self._model_name  # 在未成功调用时仍返回可观测的配置模型名。
        failed_batch_count = 0  # 统计降级为上游排序的失败小批次数。
        batches = list(_split_batches(papers, self._batch_size))  # 先按受控上限切分，避免单次发送全部候选。
        for batch_index, paper_batch in enumerate(batches, start=1):  # 依序处理批次以避免并发放大外部 API 负载。
            try:  # 单批失败不得丢弃先前或后续可核验的候选。
                batch = await self._client.assess(query, paper_batch)  # 调用 DeepSeek 核验最多十篇论文。
            except LlmAssessmentError:  # 缺少密钥、网络或无效结构均不得阻断搜索。
                failed_batch_count += 1  # 标记当前批次由上游排序降级。
                logger.exception("LLM 论文精排批次调用失败：批次=%d，候选=%d", batch_index, len(paper_batch))  # 在受控日志保留完整堆栈且不记录查询、密钥或响应正文。
                continue  # 继续执行后续批次，减少瞬时上游故障的影响范围。
            model_name = batch.model_name  # 记录最后一次成功响应的实际模型标识。
            prompt_tokens += batch.prompt_tokens  # 聚合已成功批次的输入 Token。
            completion_tokens += batch.completion_tokens  # 聚合已成功批次的输出 Token。
            for assessment in batch.assessments:  # 逐项合并每个小批次的结构化核验结果。
                if assessment.paper_id in paper_by_id and assessment.paper_id not in assessment_by_id:  # 仅接受原候选集合中首个同 ID 输出。
                    assessment_by_id[assessment.paper_id] = assessment  # 忽略模型虚构 ID 和重复项。
        if failed_batch_count == len(batches):  # 全部批次失败时保留既有完整降级语义。
            return self._fallback_result(papers, target_count)  # 沿用 Cross Encoder 顺序并截断最终数量。
        assessed_papers: list[PaperRecord] = []  # 保存绑定经过本地校验证据的候选。
        rejected_count = 0  # 统计明确不满足硬约束的候选。
        for paper in papers:  # 保持缺失核验项仍能安全降级而不意外丢失论文。
            assessment = assessment_by_id.get(paper.paper_id)  # 查找当前论文的结构化模型输出。
            if assessment is None:  # 部分输出或 ID 缺失时按不确定处理。
                assessed_papers.append(paper.model_copy(update={"constraint_status": "uncertain"}))  # 不虚构分数、证据或理由。
                continue  # 继续处理其余有效核验项。
            if assessment.relevance_score < self._minimum_relevance_score:  # 低相关论文无需依赖否定证据即可退出推荐集合。
                rejected_count += 1  # 将低相关淘汰计入最终核验统计。
                continue  # 避免零分或近零分论文展示为“需要进一步核验”。
            valid_evidence = _validated_evidence(paper, assessment.evidence)  # 只保留能在公开元数据中逐字定位的片段。
            status = _safe_status(assessment.constraint_status, valid_evidence)  # 无有效证据时禁止声称硬约束已满足。
            if assessment.relevance_score < HIGH_RELEVANCE_SCORE and status == "satisfied":  # 0.35–0.59 虽可保留，但不得按高相关计入覆盖完成度。
                status = "uncertain"  # 将中等相关候选稳定归入部分相关，避免约束证据掩盖较弱主题相关性。
            if status == "not_satisfied":  # LLM 明确发现规则过滤无法识别的语义硬约束失败。
                rejected_count += 1  # 计入核验淘汰统计。
                continue  # 不将明确失败论文放入最终推荐集合。
            assessed_papers.append(  # 将核验字段写回兼容演进的论文模型。
                paper.model_copy(
                    update={
                        "llm_relevance_score": assessment.relevance_score,  # 保存本批归一化相关性分数。
                        "constraint_status": status,  # 保存经本地证据守卫修正的状态。
                        "constraint_evidence": valid_evidence,  # 保存可由前端展示和定位的证据片段。
                        "recommendation_reason": assessment.recommendation_reason if valid_evidence else None,  # 没有可信证据时不展示生成理由。
                    }
                )
            )
        ranked_papers = sorted(assessed_papers, key=_ranking_key)  # 先按相关性分层分数排序，约束状态只在相关性并列时打破平局。
        retained_papers = ranked_papers[:target_count]  # 截断为用户目标和系统上限中的较小值。
        result = LlmRankingResult(  # 构造含 Token、淘汰和截断统计的正常结果。
            papers=retained_papers,
            input_count=len(papers),
            truncated_count=max(0, len(ranked_papers) - len(retained_papers)),
            rejected_count=rejected_count,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            ranking_error="LLM 精排部分批次不可用，未核验论文已沿用上游排序" if failed_batch_count else None,
        )
        logger.info("LLM 精排完成：输入=%d，批次=%d，失败批次=%d，约束淘汰=%d，截断=%d，最终=%d，输入Token=%d，输出Token=%d", result.input_count, len(batches), failed_batch_count, result.rejected_count, result.truncated_count, len(result.papers), result.prompt_tokens, result.completion_tokens)  # 只记录数量与用量统计。
        return result  # 返回证据化最终论文结果。

    def _fallback_result(self, papers: list[PaperRecord], target_count: int) -> LlmRankingResult:
        """在 LLM 不可用时沿用 Cross Encoder 顺序并截断最终候选。"""
        retained_papers = papers[:target_count]  # 输入已按 Cross Encoder 排序，无需重新猜测相关性。
        result = LlmRankingResult(papers=retained_papers, input_count=len(papers), truncated_count=max(0, len(papers) - len(retained_papers)), model_name=self._model_name, ranking_error="LLM 精排不可用，已沿用 Cross Encoder 排序")  # 返回不含底层错误的稳定摘要。
        logger.warning("LLM 精排降级：输入=%d，截断=%d，最终=%d", result.input_count, result.truncated_count, len(result.papers))  # 不记录查询或底层响应。
        return result  # 保持搜索链路可用且最多返回二十篇。

    def _disabled_result(self, papers: list[PaperRecord], target_count: int) -> LlmRankingResult:
        """在用户显式关闭 LLM 精排时沿用 Cross Encoder 顺序并截断结果。"""

        retained_papers = papers[:target_count]  # 上游已按 Cross Encoder、BGE-M3 或 RRF 形成稳定顺序。
        result = LlmRankingResult(papers=retained_papers, input_count=len(papers), truncated_count=max(0, len(papers) - len(retained_papers)), model_name=self._model_name, ranking_error="LLM 精排已按配置跳过，已沿用上游排序")  # 返回不含模型调用的稳定结果。
        logger.info("LLM 精排已按配置跳过：输入=%d，最终=%d", result.input_count, len(result.papers))  # 记录节省外部 API 等待和 Token 的安全统计。
        return result  # 保持最终响应契约与降级路径一致。


def _validated_evidence(paper: PaperRecord, evidence_items: list[str]) -> list[str]:
    """仅保留可在论文公开元数据中逐字找到的非重复证据片段。"""
    searchable_text = "\n".join(  # 汇总允许模型引用的公开元数据字段。
        part
        for part in (
            paper.title,
            paper.abstract,
            " ".join(paper.keywords),
            " ".join(author.name for author in paper.authors),
            " ".join(author.institution or "" for author in paper.authors),
            str(paper.year or ""),
            paper.venue or "",
            paper.paper_type or "",
        )
        if part
    ).casefold()  # 使用大小写无关比较但保留模型返回的原始展示文本。
    valid_items: list[str] = []  # 按模型顺序保存可定位且不重复的证据。
    seen_items: set[str] = set()  # 防止同一片段重复展示。
    for item in evidence_items:  # 逐条验证模型声称的证据。
        normalized_item = item.strip()  # 忽略无意义首尾空白。
        comparison_item = normalized_item.casefold()  # 使用大小写无关的精确子串检查。
        if normalized_item and comparison_item in searchable_text and comparison_item not in seen_items:  # 只接受真实存在的首次片段。
            valid_items.append(normalized_item)  # 保留原始大小写供前端展示。
            seen_items.add(comparison_item)  # 标记规范化片段已使用。
    return valid_items  # 返回经过本地事实守卫的证据集合。


def _safe_status(status: ConstraintMatchStatus, valid_evidence: list[str]) -> ConstraintMatchStatus:
    """在缺少可定位证据时将肯定或否定结论统一降级为“不确定”。"""
    if status != "uncertain" and not valid_evidence:  # 禁止无证据的肯定或淘汰结论进入 API。
        return "uncertain"  # 交由前端提示用户进一步核验。
    return status  # 结论有证据或模型已承认不确定时保留原状态。


def _ranking_key(paper: PaperRecord) -> tuple[float, float, float, float, float, str]:
    """构造相关性优先、约束状态仅作同分决策的稳定排序键。"""
    status_priority = 0.0 if paper.constraint_status == "satisfied" else 1.0  # 仅在所有相关性层分数相同后，优先有证据支持的约束状态。
    return (-_score_or_negative_infinity(paper.llm_relevance_score), -_score_or_negative_infinity(paper.cross_encoder_score), -_score_or_negative_infinity(paper.semantic_score), -paper.rrf_score, status_priority, paper.paper_id)  # 相关性永远优先于约束展示状态，再以来源融合分数、状态和标识稳定消除并列。


def _split_batches(papers: list[PaperRecord], batch_size: int) -> list[list[PaperRecord]]:
    """按受控数量将论文候选切分为顺序核验小批次。

    参数：
        papers：已由上游排序的候选论文。
        batch_size：每个 DeepSeek 请求允许携带的最大论文数。

    返回：
        list[list[PaperRecord]]：保持原排序的非空小批次列表。
    """
    return [papers[index : index + batch_size] for index in range(0, len(papers), batch_size)]  # 使用切片保留上游排序且不修改输入列表。


def _score_or_negative_infinity(score: float | None) -> float:
    """将缺失分数转换为负无穷，保证降级候选排序稳定。"""
    return score if score is not None else float("-inf")  # 已知分数始终优先于缺失分数。
