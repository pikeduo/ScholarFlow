"""提供 Cross Encoder 精细重排、稳定降级与候选截断服务。"""

from backend.app.adapters.cross_encoder import BgeCrossEncoder, CrossEncoderError, CrossEncoderScorer  # 使用可替换的 Cross Encoder 适配边界。
from backend.app.core.logging import logger  # 记录不含查询和论文正文的重排统计。
from backend.app.models.cross_encoder_ranking import CrossEncoderRankingResult  # 返回重排候选、截断统计和降级状态。
from backend.app.models.paper import PaperRecord  # 使用 BGE-M3 粗排后的融合论文记录。
from backend.app.models.query_intent import QueryIntent  # 使用规范化查询文本构造 Cross Encoder 输入。


DEFAULT_CROSS_ENCODER_CANDIDATE_LIMIT = 24  # 为后续 LLM 核验保留约 20 至 40 篇精排候选的标准模式上限。


class CrossEncoderReranker:
    """通过 Cross Encoder 细化 BGE-M3 候选顺序，并在模型不可用时安全降级。"""

    def __init__(self, scorer: CrossEncoderScorer | None = None, candidate_limit: int = DEFAULT_CROSS_ENCODER_CANDIDATE_LIMIT, model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
        """保存可替换打分器和候选上限，不在构造阶段下载模型。"""
        if candidate_limit < 1:  # 零或负上限会使后续 LLM 核验失去全部候选。
            raise ValueError("candidate_limit 必须大于零")  # 在服务装配阶段尽早暴露无效策略。
        self._scorer = scorer or BgeCrossEncoder(model_name=model_name)  # 默认使用官方 BGE Cross Encoder，测试可注入替身。
        self._candidate_limit = candidate_limit  # 保存 Cross Encoder 后进入 LLM 的最大候选数。
        self._model_name = model_name  # 保存可观测模型名称而不暴露本地路径。

    def rerank(self, papers: list[PaperRecord], query: QueryIntent) -> CrossEncoderRankingResult:
        """按 Cross Encoder 分数重排并截断；模型不可用时按 BGE-M3 分数降级。"""
        if not papers:  # 空候选不需要模型加载。
            return CrossEncoderRankingResult(papers=[], input_count=0, truncated_count=0, model_name=self._model_name)  # 返回稳定空结果。
        document_texts = [_build_document_text(paper) for paper in papers]  # 构造标题、摘要和关键词组成的论文文本。
        try:  # 将模型不可用转为可继续检索的安全降级。
            scores = self._scorer.score(query.normalized_query, document_texts)  # 批量计算查询-论文精细相关性。
        except CrossEncoderError:  # 模型依赖、权重或设备不可用时使用 BGE-M3 顺序降级。
            return self._fallback_result(papers)  # 保留候选且不阻断检索链路。
        if len(scores) != len(papers):  # 防止错配分数污染论文结果。
            logger.error("Cross Encoder 分数长度不匹配：论文=%d，分数=%d", len(papers), len(scores))  # 仅记录数量统计。
            return self._fallback_result(papers)  # 使用稳定降级顺序避免返回错误结果。
        scored_papers = [paper.model_copy(update={"cross_encoder_score": float(score)}) for paper, score in zip(papers, scores, strict=True)]  # 将精细分数写入兼容的新增字段。
        ranked_papers = sorted(scored_papers, key=lambda paper: (-_score_or_negative_infinity(paper.cross_encoder_score), -_score_or_negative_infinity(paper.semantic_score), -paper.rrf_score, paper.paper_id))  # Cross Encoder 为主，BGE、RRF 和 ID 为稳定次级键。
        retained_papers = ranked_papers[:self._candidate_limit]  # 截断为 LLM 核验可处理的候选规模。
        result = CrossEncoderRankingResult(papers=retained_papers, input_count=len(papers), truncated_count=len(papers) - len(retained_papers), model_name=self._model_name)  # 构造正常精排结果。
        logger.info("Cross Encoder 重排完成：输入=%d，截断=%d，保留=%d", result.input_count, result.truncated_count, len(result.papers))  # 记录阶段数量统计。
        return result  # 返回携带精细分数的稳定候选。

    def _fallback_result(self, papers: list[PaperRecord]) -> CrossEncoderRankingResult:
        """在 Cross Encoder 不可用时按 BGE-M3、RRF 和 ID 稳定降级并截断。"""
        fallback_papers = sorted(papers, key=lambda paper: (-_score_or_negative_infinity(paper.semantic_score), -paper.rrf_score, paper.paper_id))[:self._candidate_limit]  # 复用已有语义粗排顺序作为无模型降级依据。
        result = CrossEncoderRankingResult(papers=fallback_papers, input_count=len(papers), truncated_count=len(papers) - len(fallback_papers), model_name=self._model_name, ranking_error="Cross Encoder 重排不可用，已按 BGE-M3 分数降级")  # 返回不含底层错误细节的安全摘要。
        logger.warning("Cross Encoder 重排降级：输入=%d，截断=%d，保留=%d", result.input_count, result.truncated_count, len(result.papers))  # 记录安全降级统计。
        return result  # 返回仍可交给 LLM 核验的稳定候选。


def _build_document_text(paper: PaperRecord) -> str:
    """组合公开标题、摘要和关键词，作为 Cross Encoder 的论文侧输入。"""
    return "\n".join(part for part in (paper.title, paper.abstract, " ".join(paper.keywords)) if part.strip())  # 不使用网页发现或原始响应内容，保持证据边界。


def _score_or_negative_infinity(score: float | None) -> float:
    """将缺失分数转换为负无穷，确保降级排序可比较且稳定。"""
    return score if score is not None else float("-inf")  # 已知分数始终优先于缺失分数。
