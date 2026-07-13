"""验证 BGE-M3 语义粗排的分数排序、候选截断与安全降级。"""

import pytest  # 提供无效候选上限的异常断言。

from backend.app.adapters.bge_m3 import BgeM3EncoderError  # 构造不依赖真实模型的降级场景。
from backend.app.models.paper import PaperRecord  # 构造已融合的论文候选。
from backend.app.models.query_intent import QueryIntent  # 构造统一查询意图。
from backend.app.services.semantic_ranking import SemanticRanker  # 导入待测语义粗排服务。


class _StubEncoder:
    """返回固定语义分数的离线 BGE-M3 编码器替身。"""

    def __init__(self, scores: list[float]) -> None:
        """保存与候选文档等长的固定分数列表。"""
        self._scores = scores  # 保存无需模型下载的预设相关性分数。

    def score(self, query_text: str, document_texts: list[str]) -> list[float]:
        """验证输入形状后返回固定分数，不加载真实模型。"""
        assert query_text == "Research topic: Transformer forecasting"  # 验证服务使用统一结构化查询文本。
        assert len(document_texts) == len(self._scores)  # 验证候选和返回分数保持一一对应。
        return self._scores  # 返回预设分数。


class _FailingEncoder:
    """模拟 BGE-M3 依赖或模型不可用的安全降级场景。"""

    def score(self, _: str, __: list[str]) -> list[float]:
        """始终抛出已净化编码器错误，不包含底层环境信息。"""
        raise BgeM3EncoderError("模型不可用")  # 触发服务按 RRF 降级。


def _query() -> QueryIntent:
    """构造用于语义粗排的最小有效检索意图。"""
    return QueryIntent(original_query="Transformer forecasting", normalized_query="Transformer forecasting", query_language="en")  # 提供语义编码所需规范化查询。


def _paper(paper_id: str, rrf_score: float) -> PaperRecord:
    """构造携带 RRF 分数的最小融合论文候选。"""
    return PaperRecord(paper_id=paper_id, title=f"Paper {paper_id}", abstract="Forecasting benchmark", source="openalex", rrf_score=rrf_score)  # 提供可生成文档文本的稳定候选。


def test_rank_orders_by_semantic_score_and_truncates_candidates() -> None:
    """BGE-M3 分数应作为主排序键，且仅保留配置上限内的候选。"""
    papers = [_paper("a", 0.1), _paper("b", 0.3), _paper("c", 0.2)]  # 构造 RRF 顺序不同于语义分数的候选。
    result = SemanticRanker(encoder=_StubEncoder([0.2, 0.9, 0.5]), candidate_limit=2).rank(papers, _query())  # 使用离线替身执行粗排。

    assert [paper.paper_id for paper in result.papers] == ["b", "c"]  # 验证按语义分数降序排序并截断。
    assert [paper.semantic_score for paper in result.papers] == [0.9, 0.5]  # 验证分数写入统一论文记录。
    assert result.truncated_count == 1  # 验证超出候选上限的论文被统计。
    assert result.ranking_error is None  # 验证正常编码不会产生降级信息。


def test_rank_falls_back_to_rrf_when_encoder_is_unavailable() -> None:
    """模型不可用时服务应保持检索可用，并按 RRF 稳定排序。"""
    papers = [_paper("a", 0.1), _paper("b", 0.3), _paper("c", 0.2)]  # 构造可由 RRF 确定排序的候选。
    result = SemanticRanker(encoder=_FailingEncoder(), candidate_limit=2).rank(papers, _query())  # 触发已净化模型错误。

    assert [paper.paper_id for paper in result.papers] == ["b", "c"]  # 验证降级后按 RRF 降序截断。
    assert result.ranking_error == "BGE-M3 语义粗排不可用，已按 RRF 降级"  # 验证调用方得到安全可展示的降级说明。


def test_rank_rejects_invalid_candidate_limit() -> None:
    """候选上限不能为零或负数，避免工作流无法向后续阶段提供候选。"""
    with pytest.raises(ValueError, match="candidate_limit"):  # 断言返回稳定配置错误。
        SemanticRanker(encoder=_StubEncoder([]), candidate_limit=0)  # 构造无效截断策略。


def test_rank_skips_bge_m3_when_fast_path_is_configured() -> None:
    """快速路径关闭 BGE-M3 后不得调用编码器，且应按 RRF 返回候选。"""
    papers = [_paper("a", 0.1), _paper("b", 0.3), _paper("c", 0.2)]  # 构造可由 RRF 确定的候选顺序。
    result = SemanticRanker(encoder=_FailingEncoder(), candidate_limit=2, enabled=False).rank(papers, _query())  # 注入会失败的编码器证明快速路径不调用它。

    assert [paper.paper_id for paper in result.papers] == ["b", "c"]  # 验证跳过模型后仍按 RRF 稳定排序并截断。
    assert result.ranking_error == "BGE-M3 语义粗排已按配置跳过，已按 RRF 排序"  # 验证前端可区分主动跳过与模型故障。
