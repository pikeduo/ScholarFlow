"""提供 BGE-M3 语义粗排、稳定降级和候选截断服务。"""

from backend.app.adapters.bge_m3 import BgeM3Encoder, BgeM3EncoderError, SemanticTextEncoder  # 使用可替换的本地语义编码边界。
from backend.app.core.logging import logger  # 记录不包含查询和论文文本的语义排序统计。
from backend.app.models.paper import PaperRecord  # 使用规则过滤后的融合论文记录。
from backend.app.models.query_intent import QueryIntent  # 使用规范化查询文本进行跨语言语义匹配。
from backend.app.models.semantic_ranking import SemanticRankingResult  # 返回排序候选、截断统计和降级状态。


DEFAULT_SEMANTIC_CANDIDATE_LIMIT = 60  # 遵循规划书 BGE-M3 粗排阶段默认保留 40 至 60 篇候选的上界。


class SemanticRanker:
    """通过 BGE-M3 dense 向量为融合论文打分，并在模型不可用时安全保留 RRF 顺序。"""

    def __init__(self, encoder: SemanticTextEncoder | None = None, candidate_limit: int = DEFAULT_SEMANTIC_CANDIDATE_LIMIT, model_name: str = "BAAI/bge-m3") -> None:
        """保存可替换编码器和候选上限，不在构造阶段加载或下载模型。"""
        if candidate_limit < 1:  # 空或负候选上限会导致后续工作流无法产生候选。
            raise ValueError("candidate_limit 必须大于零")  # 在服务装配阶段尽早暴露无效策略。
        self._encoder = encoder or BgeM3Encoder(model_name=model_name)  # 默认使用官方 BGE-M3 懒加载编码器，测试可注入替身。
        self._candidate_limit = candidate_limit  # 保存 BGE 粗排后进入下一阶段的最大候选数。
        self._model_name = model_name  # 保存可观测的模型名称而不暴露本地路径。

    def rank(self, papers: list[PaperRecord], query: QueryIntent) -> SemanticRankingResult:
        """按语义分数粗排论文并截断；编码不可用时以 RRF 稳定降级。

        参数：
            papers：已经完成融合和确定性规则过滤的论文候选。
            query：包含规范化查询文本的统一检索意图。
        返回：
            SemanticRankingResult：排序或降级后的候选、截断统计及安全错误摘要。
        """
        if not papers:  # 空候选无需调用模型或构造文档文本。
            return SemanticRankingResult(papers=[], input_count=0, truncated_count=0, model_name=self._model_name)  # 返回稳定空结果。
        document_texts = [_build_document_text(paper) for paper in papers]  # 使用标题、摘要和关键词构造可解释论文表示。
        try:  # 仅将模型不可用映射为可降级业务结果，其他编程错误保持可见。
            scores = self._encoder.score(query.normalized_query, document_texts)  # 批量获取每篇论文对应的 dense 相关性分数。
        except BgeM3EncoderError:  # 模型依赖、权重或设备不可用时保留确定性候选。
            return self._fallback_result(papers)  # 使用 RRF 和原始顺序构造安全降级结果。
        if len(scores) != len(papers):  # 不接受编码器返回长度不一致的不可解释结果。
            logger.error("BGE-M3 分数长度不匹配：论文=%d，分数=%d", len(papers), len(scores))  # 记录数量而不输出文本内容。
            return self._fallback_result(papers)  # 使用稳定降级避免错配分数污染论文排序。
        scored_papers = [paper.model_copy(update={"semantic_score": float(score)}) for paper, score in zip(papers, scores, strict=True)]  # 将语义分数写入兼容的新增字段。
        ranked_papers = sorted(scored_papers, key=lambda paper: (-_score_or_negative_infinity(paper.semantic_score), -paper.rrf_score, paper.paper_id))  # 语义分数优先，RRF 和 ID 作为稳定次级排序。
        retained_papers = ranked_papers[:self._candidate_limit]  # 截断为后续 Cross Encoder 或 LLM 可承受的候选规模。
        result = SemanticRankingResult(papers=retained_papers, input_count=len(papers), truncated_count=len(papers) - len(retained_papers), model_name=self._model_name)  # 构造正常粗排输出。
        logger.info("BGE-M3 粗排完成：输入=%d，截断=%d，保留=%d", result.input_count, result.truncated_count, len(result.papers))  # 记录阶段数量统计。
        return result  # 返回携带语义分数的稳定候选列表。

    def _fallback_result(self, papers: list[PaperRecord]) -> SemanticRankingResult:
        """在 BGE-M3 不可用时按已有 RRF 和稳定 ID 截断，避免阻断检索闭环。"""
        fallback_papers = sorted(papers, key=lambda paper: (-paper.rrf_score, paper.paper_id))[:self._candidate_limit]  # 使用融合阶段既有 RRF 维持可解释、确定性的降级顺序。
        result = SemanticRankingResult(papers=fallback_papers, input_count=len(papers), truncated_count=len(papers) - len(fallback_papers), model_name=self._model_name, ranking_error="BGE-M3 语义粗排不可用，已按 RRF 降级")  # 返回不含底层错误详情的安全摘要。
        logger.warning("BGE-M3 粗排降级：输入=%d，截断=%d，保留=%d", result.input_count, result.truncated_count, len(result.papers))  # 记录安全降级统计。
        return result  # 返回仍可交给下一阶段的稳定候选列表。


def _build_document_text(paper: PaperRecord) -> str:
    """组合公开标题、摘要和关键词，作为 BGE-M3 的论文语义表示。"""
    return "\n".join(part for part in (paper.title, paper.abstract, " ".join(paper.keywords)) if part.strip())  # 忽略空字段而不引入来源原始响应或网页文本。


def _score_or_negative_infinity(score: float | None) -> float:
    """将缺失分数转换为负无穷，确保排序比较保持稳定。"""
    return score if score is not None else float("-inf")  # 已写入分数的论文总是优先于异常缺失分数。
