"""验证 LLM 精排的证据守卫、约束淘汰、最终截断与安全降级。"""

import asyncio  # 在同步 pytest 用例中执行异步 LLM 精排服务。

import pytest  # 提供无效最终上限的异常断言。

from backend.app.adapters.deepseek_llm import LlmAssessmentError  # 构造不访问网络的 LLM 降级场景。
from backend.app.models.llm_ranking import LlmAssessmentBatch, LlmPaperAssessment  # 构造固定结构化模型输出。
from backend.app.models.paper import PaperRecord  # 构造 Cross Encoder 后的论文候选。
from backend.app.models.query_intent import QueryIntent  # 构造统一查询意图和最终结果目标。
from backend.app.services.llm_ranking import LlmPaperReranker  # 导入待测 LLM 最终精排服务。


class _StubAssessmentClient:
    """返回固定逐篇核验结果和 Token 统计的离线 LLM 替身。"""

    def __init__(self, assessments: list[LlmPaperAssessment]) -> None:
        """保存无需外部 API 的固定核验结果。"""
        self._assessments = assessments  # 保存测试用结构化模型输出。

    async def assess(self, _: QueryIntent, __: list[PaperRecord]) -> LlmAssessmentBatch:
        """返回固定结果并模拟供应商 Token 统计。"""
        return LlmAssessmentBatch(assessments=self._assessments, model_name="test-llm", prompt_tokens=120, completion_tokens=40)  # 构造可审计离线批次。


class _FailingAssessmentClient:
    """模拟密钥、网络或结构化输出不可用的 LLM 客户端。"""

    async def assess(self, _: QueryIntent, __: list[PaperRecord]) -> LlmAssessmentBatch:
        """始终抛出已净化异常以触发 Cross Encoder 降级。"""
        raise LlmAssessmentError("模型不可用")  # 不携带真实 URL、密钥或响应正文。


class _PartiallyFailingAssessmentClient:
    """记录小批次并只让指定批次失败的离线 LLM 替身。"""

    def __init__(self, failing_batch_index: int) -> None:
        """保存一开始计数的失败批次序号和已接收候选记录。"""
        self._failing_batch_index = failing_batch_index  # 保存需要模拟上游故障的唯一批次。
        self.received_batches: list[list[str]] = []  # 保存每次调用收到的论文 ID，供批量边界断言。

    async def assess(self, _: QueryIntent, papers: list[PaperRecord]) -> LlmAssessmentBatch:
        """为成功批次返回逐篇可定位证据，为失败批次返回净化异常。"""
        self.received_batches.append([paper.paper_id for paper in papers])  # 记录本次小批次而不访问外部网络。
        if len(self.received_batches) == self._failing_batch_index:  # 在指定批次模拟瞬时上游失败。
            raise LlmAssessmentError("模拟批次超时")  # 触发服务的局部降级分支。
        assessments = [  # 为成功批次中的每篇论文生成可由本地证据守卫验证的结果。
            LlmPaperAssessment(paper_id=paper.paper_id, relevance_score=0.9, constraint_status="satisfied", evidence=["ETT benchmark"], recommendation_reason="具有可定位的基准证据。")
            for paper in papers
        ]
        return LlmAssessmentBatch(assessments=assessments, model_name="test-llm", prompt_tokens=10, completion_tokens=5)  # 返回固定且可累计的批次用量。


def _query(target_paper_count: int = 20) -> QueryIntent:
    """构造用于最终精排的最小查询意图。"""
    return QueryIntent(original_query="Transformer forecasting", normalized_query="Transformer forecasting", query_language="en", must_include=["Transformer"], target_paper_count=target_paper_count)  # 提供硬约束和目标结果数。


def _paper(paper_id: str, cross_score: float) -> PaperRecord:
    """构造携带公开证据与 Cross Encoder 分数的候选论文。"""
    return PaperRecord(paper_id=paper_id, title=f"Transformer Forecasting {paper_id}", abstract="Evaluation on ETT benchmark.", source="openalex", cross_encoder_score=cross_score, semantic_score=cross_score, rrf_score=0.01)  # 提供证据守卫可定位的文本。


def test_rerank_binds_valid_evidence_rejects_failed_constraint_and_truncates() -> None:
    """服务应绑定真实证据、淘汰有证据的失败论文并按最终目标截断。"""
    papers = [_paper("a", 0.9), _paper("b", 0.8), _paper("c", 0.7)]  # 构造三篇已完成 Cross Encoder 排序的候选。
    assessments = [  # 构造顺序与输入不同的结构化核验结果。
        LlmPaperAssessment(paper_id="b", relevance_score=0.99, constraint_status="satisfied", evidence=["ETT benchmark"], recommendation_reason="在 ETT 基准上验证了预测方法。"),  # 提供有效摘要证据。
        LlmPaperAssessment(paper_id="a", relevance_score=0.8, constraint_status="satisfied", evidence=["不存在的证据"], recommendation_reason="该理由不应展示。"),  # 模拟无法定位的幻觉证据。
        LlmPaperAssessment(paper_id="c", relevance_score=0.7, constraint_status="not_satisfied", evidence=["Transformer Forecasting c"], recommendation_reason="不满足目标约束。"),  # 提供可定位的淘汰证据。
    ]
    result = asyncio.run(LlmPaperReranker(client=_StubAssessmentClient(assessments), result_limit=20).rerank(papers, _query(target_paper_count=1)))  # 执行离线核验和最终截断。

    assert [paper.paper_id for paper in result.papers] == ["b"]  # 验证满足约束且分数最高的论文成为唯一最终结果。
    assert result.papers[0].constraint_evidence == ["ETT benchmark"]  # 验证仅保留可在公开元数据定位的证据。
    assert result.papers[0].recommendation_reason == "在 ETT 基准上验证了预测方法。"  # 验证可信证据支撑的推荐理由被保留。
    assert result.rejected_count == 1  # 验证明确失败候选被统计而不进入最终结果。
    assert result.truncated_count == 1  # 验证另一篇未被淘汰的候选因目标数量被截断。
    assert result.prompt_tokens == 120 and result.completion_tokens == 40  # 验证 Token 统计向上游透传。


def test_rerank_downgrades_claim_without_valid_evidence_to_uncertain() -> None:
    """肯定或否定结论缺少可定位证据时都不能决定推荐或淘汰。"""
    paper = _paper("a", 0.9)  # 构造单篇可保留候选。
    assessment = LlmPaperAssessment(paper_id="a", relevance_score=0.9, constraint_status="satisfied", evidence=["幻觉片段"], recommendation_reason="无证据理由")  # 构造无效证据声明。
    result = asyncio.run(LlmPaperReranker(client=_StubAssessmentClient([assessment])).rerank([paper], _query()))  # 执行本地证据守卫。

    assert result.papers[0].constraint_status == "uncertain"  # 验证无证据肯定结论降级为不确定。
    assert result.papers[0].constraint_evidence == []  # 验证幻觉证据被删除。
    assert result.papers[0].recommendation_reason is None  # 验证无可信证据时不展示生成理由。


def test_rerank_falls_back_to_cross_encoder_order_when_llm_is_unavailable() -> None:
    """LLM 不可用时服务应沿用输入顺序并仍限制最终结果数量。"""
    papers = [_paper("a", 0.9), _paper("b", 0.8), _paper("c", 0.7)]  # 输入顺序代表既有 Cross Encoder 排序。
    result = asyncio.run(LlmPaperReranker(client=_FailingAssessmentClient(), result_limit=2).rerank(papers, _query()))  # 触发安全降级。

    assert [paper.paper_id for paper in result.papers] == ["a", "b"]  # 验证不重新猜测排序且执行最终截断。
    assert result.ranking_error == "LLM 精排不可用，已沿用 Cross Encoder 排序"  # 验证返回安全可展示的降级摘要。
    assert result.truncated_count == 1  # 验证降级路径也记录候选截断数量。


def test_rerank_continues_remaining_small_batches_after_one_batch_fails() -> None:
    """单个 DeepSeek 小批次超时时，其余批次仍应完成核验并累计成功用量。"""
    papers = [_paper(f"paper-{index}", 1.0 - index / 100) for index in range(12)]  # 构造需按五篇边界切成三批的有序候选。
    client = _PartiallyFailingAssessmentClient(failing_batch_index=2)  # 仅让第二批模拟上游超时。

    result = asyncio.run(LlmPaperReranker(client=client, batch_size=5).rerank(papers, _query(target_paper_count=12)))  # 执行局部失败但不中断后续批次的核验。

    assert [len(batch) for batch in client.received_batches] == [5, 5, 2]  # 验证单次请求绝不超过配置的五篇上限。
    assert result.prompt_tokens == 20 and result.completion_tokens == 10  # 验证仅累计两个成功批次的 Token 用量。
    assert result.ranking_error == "LLM 精排部分批次不可用，未核验论文已沿用上游排序"  # 验证前端可识别局部而非完整降级。
    assert result.papers[-1].paper_id == "paper-9"  # 验证失败批论文在已核验论文之后沿用上游顺序保留。
    assert result.papers[-1].constraint_status == "uncertain"  # 验证失败批不伪造已满足约束状态。


def test_rerank_returns_empty_result_without_calling_llm() -> None:
    """空候选应直接返回且不需要外部模型调用。"""
    result = asyncio.run(LlmPaperReranker(client=_FailingAssessmentClient()).rerank([], _query()))  # 即使客户端会失败也不应被调用。

    assert result.papers == [] and result.input_count == 0  # 验证稳定空结果。
    assert result.ranking_error is None  # 验证空结果不是模型错误。


def test_rerank_skips_deepseek_when_fast_path_is_configured() -> None:
    """快速路径关闭 LLM 后不得调用 DeepSeek，且应沿用上游顺序。"""
    papers = [_paper("a", 0.9), _paper("b", 0.8), _paper("c", 0.7)]  # 输入顺序代表已有上游重排结果。
    result = asyncio.run(LlmPaperReranker(client=_FailingAssessmentClient(), result_limit=2, enabled=False).rerank(papers, _query()))  # 注入会失败客户端证明快速路径不发起外部 API 调用。

    assert [paper.paper_id for paper in result.papers] == ["a", "b"]  # 验证跳过模型后保留上游顺序并截断。
    assert result.ranking_error == "LLM 精排已按配置跳过，已沿用上游排序"  # 验证结果明确标记为用户配置的主动跳过。
    assert result.prompt_tokens == 0 and result.completion_tokens == 0  # 验证没有模型调用时不产生 Token 用量。


def test_rerank_rejects_low_relevance_paper_without_negative_evidence() -> None:
    """低相关候选应直接退出最终结果，避免零分论文显示为待核验。"""
    paper = _paper("a", 0.9)  # 构造上游排序候选。
    assessment = LlmPaperAssessment(paper_id="a", relevance_score=0.0, constraint_status="uncertain", evidence=[], recommendation_reason="相关性不足")  # 构造零分且无否定证据的核验结果。
    result = asyncio.run(LlmPaperReranker(client=_StubAssessmentClient([assessment]), minimum_relevance_score=0.35).rerank([paper], _query()))  # 执行新的最低相关度过滤。

    assert result.papers == []  # 验证零分论文不再透传到前端。
    assert result.rejected_count == 1  # 验证低相关淘汰进入统计。


def test_rerank_prioritizes_relevance_over_satisfied_constraint_status() -> None:
    """高相关但待核验论文必须排在低相关的“约束已满足”论文之前。"""
    papers = [_paper("high-uncertain", 0.7), _paper("partial-satisfied", 0.9)]  # 构造上游分数故意更高但 LLM 相关度处于部分相关范围的候选。
    assessments = [  # 构造可定位证据与不同的 LLM 相关度，覆盖最终排序冲突。
        LlmPaperAssessment(paper_id="high-uncertain", relevance_score=0.9, constraint_status="uncertain", evidence=[], recommendation_reason="相关性高但部分约束仍需核验。"),  # 高相关候选不需要伪造证据来表示不确定。
        LlmPaperAssessment(paper_id="partial-satisfied", relevance_score=0.35, constraint_status="satisfied", evidence=["ETT benchmark"], recommendation_reason="具备可定位的 ETT 证据。"),  # 边界分数候选应保留为部分相关范围。
    ]

    result = asyncio.run(LlmPaperReranker(client=_StubAssessmentClient(assessments)).rerank(papers, _query()))  # 执行最终核验和排序。

    assert [paper.paper_id for paper in result.papers] == ["high-uncertain", "partial-satisfied"]  # 验证相关度 0.9 优先于 0.35 边界候选，状态不得越级排序。
    assert result.papers[1].constraint_status == "uncertain"  # 验证 0.35–0.59 即使存在约束证据也统一展示为部分相关。


def test_rerank_rejects_invalid_result_limit() -> None:
    """最终结果上限不能为零或负数。"""
    with pytest.raises(ValueError, match="result_limit"):  # 断言服务装配时给出稳定配置错误。
        LlmPaperReranker(client=_StubAssessmentClient([]), result_limit=0)  # 构造无效最终截断策略。
    with pytest.raises(ValueError, match="batch_size"):  # 断言服务装配时拒绝超过阶段规划的批次规模。
        LlmPaperReranker(client=_StubAssessmentClient([]), batch_size=11)  # 构造会重现大批量超时风险的无效策略。
