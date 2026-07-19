"""提供只消费封存候选的 DeepSeek 异步评测核验适配器。"""

from dataclasses import dataclass  # 保存不混入同步本地打分契约的核验结果。
from time import perf_counter  # 统计真实 LLM 核验阶段耗时。
from typing import Protocol  # 隔离生产核验器与离线执行器。

from backend.app.models.paper import PaperAuthor, PaperRecord  # 复用生产证据核验所需的统一论文模型。
from backend.app.models.query_intent import QueryIntent  # 复用生产 QueryIntent 约束边界。
from backend.app.models.llm_ranking import LlmRankingResult  # 保留真实 Token、费用和降级摘要。
from evaluation.contracts.snapshot import CandidatePaper  # 只读取已封存的排序前论文候选。


class DeepSeekPaperReranker(Protocol):
    """声明评测适配器需要的最小异步生产核验协议。"""

    async def rerank(self, papers: list[PaperRecord], query: QueryIntent) -> LlmRankingResult:
        """核验已排序论文并返回证据化的最终结果与实际用量。"""
        ...  # 生产实现和零网络替身均可注入。


@dataclass(frozen=True, slots=True)
class DeepSeekOfflineResult:
    """保存 DeepSeek 核验后的候选顺序及真实调用审计信息。"""

    papers: list[CandidatePaper]  # 保存按生产核验结果排序且仍来自原快照的候选。
    input_count: int  # 保存进入 DeepSeek 的候选数量。
    output_count: int  # 保存核验后保留的候选数量。
    call_count: int  # 保存生产核验器报告的实际小批次调用尝试数。
    prompt_tokens: int  # 保存供应商报告的输入 Token。
    completion_tokens: int  # 保存供应商报告的输出 Token。
    estimated_cost_cny: float  # 保存调用时冻结的人民币费用。
    model_name: str  # 保存实际或降级模型名。
    latency_ms: float  # 保存核验端到端耗时。
    ranking_error: str | None  # 保存不泄露底层响应的降级摘要。


class DeepSeekOfflineReranker:
    """将候选快照适配为生产 DeepSeek 核验输入，不调用学术 API。"""

    def __init__(self, reranker: DeepSeekPaperReranker) -> None:
        """保存已由调用方显式装配的异步核验器。"""
        self._reranker = reranker  # 构造期不读取配置、不创建客户端也不调用模型。

    async def rerank(self, query_intent: QueryIntent, papers: list[CandidatePaper]) -> DeepSeekOfflineResult:
        """核验封存候选，并只按原候选 ID 映射生产返回结果。"""
        if not papers:  # 空快照不应消耗任何 Token。
            return DeepSeekOfflineResult(papers=[], input_count=0, output_count=0, call_count=0, prompt_tokens=0, completion_tokens=0, estimated_cost_cny=0.0, model_name="deepseek-v4-flash", latency_ms=0.0, ranking_error=None)  # 返回稳定零用量结果。
        source_by_id = {paper.paper_id: paper for paper in papers}  # 建立白名单，禁止生产返回注入未知论文。
        production_papers = [_to_production_paper(paper) for paper in papers]  # 仅映射快照已封存公开字段。
        started_at = perf_counter()  # 从真正提交核验前开始统计。
        result = await self._reranker.rerank(production_papers, query_intent)  # 唯一可能调用 DeepSeek 的边界。
        retained = [source_by_id[paper.paper_id] for paper in result.papers if paper.paper_id in source_by_id]  # 只保留原候选并采用生产核验顺序。
        return DeepSeekOfflineResult(papers=retained, input_count=result.input_count, output_count=len(retained), call_count=result.call_count, prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens, estimated_cost_cny=result.estimated_cost_cny, model_name=result.model_name, latency_ms=(perf_counter() - started_at) * 1000.0, ranking_error=result.ranking_error)  # 返回完整可审计的离线核验结果。


def _to_production_paper(paper: CandidatePaper) -> PaperRecord:
    """将封存候选转换为生产 LLM 核验所需的统一论文记录。"""
    return PaperRecord(paper_id=paper.paper_id, title=paper.title, abstract=paper.abstract, authors=[PaperAuthor(name=author) for author in paper.authors], year=paper.year, venue=paper.venue, doi=paper.doi, arxiv_id=paper.arxiv_id, pmid=paper.pmid, openalex_id=paper.openalex_id, semantic_scholar_id=paper.semantic_scholar_id, dblp_key=paper.dblp_key, source=paper.source, url=paper.url, keywords=list(paper.keywords), rrf_score=paper.rrf_score)  # 候选快照契约未封存 paper_type，生产模型使用默认空值且不得凭空推断。
