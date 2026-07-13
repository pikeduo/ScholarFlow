"""验证 Cross Encoder 重排的精细排序、候选截断与安全降级。"""

import pytest  # 提供无效候选上限的异常断言。

from backend.app.adapters.cross_encoder import CrossEncoderError  # 构造不依赖真实模型的降级场景。
from backend.app.models.paper import PaperRecord  # 构造 BGE-M3 粗排后的论文候选。
from backend.app.models.query_intent import QueryIntent  # 构造统一查询意图。
from backend.app.services.cross_encoder_ranking import CrossEncoderReranker  # 导入待测 Cross Encoder 重排服务。


class _StubScorer:
    """返回固定 Cross Encoder 分数的离线打分器替身。"""

    def __init__(self, scores: list[float]) -> None:
        """保存与候选文档等长的固定精细相关性分数。"""
        self._scores = scores  # 保存无需模型下载的预设分数。

    def score(self, query_text: str, document_texts: list[str]) -> list[float]:
        """验证输入形状后返回固定分数，不加载真实 Cross Encoder。"""
        assert query_text == "Research topic: Transformer forecasting"  # 验证服务使用统一结构化查询文本。
        assert len(document_texts) == len(self._scores)  # 验证候选和返回分数保持一一对应。
        return self._scores  # 返回预设精细相关性分数。


class _FailingScorer:
    """模拟 Cross Encoder 依赖或模型不可用的安全降级场景。"""

    def score(self, _: str, __: list[str]) -> list[float]:
        """始终抛出已净化错误，不包含模型路径或设备细节。"""
        raise CrossEncoderError("模型不可用")  # 触发服务按 BGE-M3 分数降级。


def _query() -> QueryIntent:
    """构造用于 Cross Encoder 重排的最小有效检索意图。"""
    return QueryIntent(original_query="Transformer forecasting", normalized_query="Transformer forecasting", query_language="en")  # 提供重排所需规范化查询。


def _paper(paper_id: str, semantic_score: float, rrf_score: float) -> PaperRecord:
    """构造携带 BGE-M3 和 RRF 分数的最小融合论文候选。"""
    return PaperRecord(paper_id=paper_id, title=f"Paper {paper_id}", abstract="Forecasting benchmark", source="openalex", semantic_score=semantic_score, rrf_score=rrf_score)  # 提供可构造 Cross Encoder 文本的稳定候选。


def test_rerank_orders_by_cross_encoder_score_and_truncates_candidates() -> None:
    """Cross Encoder 分数应作为主排序键，并截断为 LLM 可处理的候选规模。"""
    papers = [_paper("a", 0.9, 0.1), _paper("b", 0.5, 0.3), _paper("c", 0.7, 0.2)]  # 构造 BGE 顺序不同于 Cross Encoder 分数的候选。
    result = CrossEncoderReranker(scorer=_StubScorer([0.2, 0.95, 0.6]), candidate_limit=2).rerank(papers, _query())  # 使用离线替身执行精排。

    assert [paper.paper_id for paper in result.papers] == ["b", "c"]  # 验证按 Cross Encoder 分数降序排序并截断。
    assert [paper.cross_encoder_score for paper in result.papers] == [0.95, 0.6]  # 验证精细分数写入统一论文记录。
    assert result.truncated_count == 1  # 验证超出重排上限的论文被统计。
    assert result.ranking_error is None  # 验证正常打分不会产生降级信息。


def test_rerank_falls_back_to_semantic_score_when_scorer_is_unavailable() -> None:
    """Cross Encoder 不可用时服务应保持检索可用，并按 BGE-M3 分数稳定排序。"""
    papers = [_paper("a", 0.9, 0.1), _paper("b", 0.5, 0.3), _paper("c", 0.7, 0.2)]  # 构造可由 BGE-M3 确定顺序的候选。
    result = CrossEncoderReranker(scorer=_FailingScorer(), candidate_limit=2).rerank(papers, _query())  # 触发已净化模型错误。

    assert [paper.paper_id for paper in result.papers] == ["a", "c"]  # 验证降级后按 BGE-M3 分数降序截断。
    assert result.ranking_error == "Cross Encoder 重排不可用，已按 BGE-M3 分数降级"  # 验证调用方获得安全可展示的降级说明。


def test_rerank_rejects_invalid_candidate_limit() -> None:
    """候选上限不能为零或负数，避免后续 LLM 核验没有候选。"""
    with pytest.raises(ValueError, match="candidate_limit"):  # 断言返回稳定配置错误。
        CrossEncoderReranker(scorer=_StubScorer([]), candidate_limit=0)  # 构造无效截断策略。


def test_rerank_skips_cross_encoder_when_fast_path_is_configured() -> None:
    """快速路径关闭 Cross Encoder 后不得调用打分器，且应沿用语义排序。"""
    papers = [_paper("a", 0.9, 0.1), _paper("b", 0.5, 0.3), _paper("c", 0.7, 0.2)]  # 构造既有 BGE-M3 分数顺序。
    result = CrossEncoderReranker(scorer=_FailingScorer(), candidate_limit=2, enabled=False).rerank(papers, _query())  # 注入会失败的打分器证明快速路径不调用它。

    assert [paper.paper_id for paper in result.papers] == ["a", "c"]  # 验证跳过模型后沿用 BGE-M3 排序并截断。
    assert result.ranking_error == "Cross Encoder 重排已按配置跳过，已沿用 BGE-M3 或 RRF 排序"  # 验证前端可区分主动跳过与模型故障。
