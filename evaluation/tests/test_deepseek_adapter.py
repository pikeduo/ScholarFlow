"""验证封存候选到生产 DeepSeek 核验输入的离线字段映射。"""

import asyncio  # 在同步测试中驱动纯内存异步替身。
from backend.app.models.llm_ranking import LlmRankingResult  # 构造已审计的生产核验返回值。
from backend.app.models.query_intent import QueryIntent  # 构造最小生产查询契约。
from evaluation.adapters.deepseek import DeepSeekOfflineReranker, _to_production_paper  # 验证离线适配与纯字段映射边界。
from evaluation.contracts.snapshot import CandidatePaper  # 构造与候选快照契约一致的最小输入。


def test_candidate_snapshot_mapping_does_not_require_unstored_paper_type() -> None:
    """候选快照未封存 paper_type 时，适配器应使用生产契约默认值。"""
    candidate = CandidatePaper(  # 构造不包含 paper_type 的合法排序前候选。
        paper_id="openalex:W1",  # 提供稳定论文标识。
        title="Offline DeepSeek mapping",  # 提供生产论文契约要求的标题。
        source="openalex",  # 使用生产来源枚举中的合法值。
        abstract="A public abstract.",  # 验证公开摘要继续透传。
        authors=["Ada Lovelace"],  # 验证字符串作者转换为生产作者对象。
        year=2024,  # 验证年份透传。
        venue="TestConf",  # 验证场地透传。
        doi="10.1000/test",  # 验证强身份字段透传。
        rrf_score=0.5,  # 验证排序前融合分透传。
        snapshot_rank=1,  # 满足候选快照排序契约。
    )

    paper = _to_production_paper(candidate)  # 纯内存转换，不读取 .env、网络或模型。

    assert paper.paper_type is None  # 不从不存在的快照字段猜测论文类型。
    assert paper.authors[0].name == "Ada Lovelace"  # 保留作者展示名称。
    assert paper.rrf_score == 0.5  # 保留上游确定性融合分数。


class _StubProductionReranker:
    """返回固定结果的零网络生产核验替身。"""

    async def rerank(self, papers, _query_intent) -> LlmRankingResult:
        """按输入顺序返回论文，并模拟两次真实批次调用。"""
        return LlmRankingResult(papers=papers, input_count=len(papers), model_name="stub-deepseek", call_count=2, prompt_tokens=11, completion_tokens=7, estimated_cost_cny=0.01)  # 不访问网络或读取配置。


def test_offline_adapter_preserves_actual_production_call_count() -> None:
    """离线适配器应透传生产核验器记录的真实批次调用数。"""
    candidate = CandidatePaper(paper_id="openalex:W2", title="Call count", source="openalex", rrf_score=0.1, snapshot_rank=1)  # 构造最小候选。
    reranker = DeepSeekOfflineReranker(_StubProductionReranker())  # 注入纯内存替身，不装配真实客户端。
    query_intent = QueryIntent(original_query="offline test", normalized_query="offline test", query_language="en")  # 只提供公开合成查询。
    result = asyncio.run(reranker.rerank(query_intent, [candidate]))  # 执行不联网适配。

    assert result.call_count == 2  # 验证结果不会把多批调用压缩为一次实验调用。
