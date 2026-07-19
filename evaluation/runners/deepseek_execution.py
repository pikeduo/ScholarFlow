"""在既有离线排序结果上受控执行异步 DeepSeek 核验。"""

from backend.app.models.query_intent import QueryIntent  # 校验快照冻结的生产查询契约。
from evaluation.adapters.deepseek import DeepSeekOfflineReranker  # 依赖独立异步核验适配器。
from evaluation.adapters.base import OfflineRankingScorer  # 复用既有本地排序依赖边界。
from evaluation.contracts.ablation import AblationExperiment, OfflineAblationResult, RankingStageTrace  # 输出可审计的 DeepSeek 阶段。
from evaluation.contracts.common import EvaluationPaper  # 将核验后候选恢复为可评分预测论文。
from evaluation.contracts.snapshot import CandidateSnapshot  # 只读取已封存共享候选。
from evaluation.runners.offline_ranking import run_offline_experiment  # 复用 BGE/Cross 的稳定排序实现。


async def run_deepseek_offline_experiment(snapshot: CandidateSnapshot, matrix_id: str, experiment: AblationExperiment, *, deepseek_reranker: DeepSeekOfflineReranker, semantic_scorer: OfflineRankingScorer | None = None, cross_encoder_scorer: OfflineRankingScorer | None = None) -> OfflineAblationResult:
    """在同一快照的本地排序结果上执行一次受控 DeepSeek 核验。"""
    if not experiment.ranking_config.deepseek_enabled:  # 调用方不能借异步入口伪造无模型实验。
        raise ValueError("异步 DeepSeek 执行器要求 ranking_config.deepseek_enabled=true")  # 明确配置边界。
    base_config = experiment.ranking_config.model_copy(update={"deepseek_enabled": False})  # 临时关闭同步本地执行器的安全拒绝。
    base_experiment = experiment.model_copy(update={"ranking_config": base_config})  # 保持 BGE/Cross 参数和实验标识不变。
    base_result = run_offline_experiment(snapshot, matrix_id, base_experiment, semantic_scorer=semantic_scorer, cross_encoder_scorer=cross_encoder_scorer)  # 复用同一快照得到确定性上游候选。
    source_by_id = {paper.paper_id: paper for paper in snapshot.papers}  # 只允许 DeepSeek 返回原快照候选。
    input_candidates = [source_by_id[paper.paper_id] for paper in base_result.prediction.papers if paper.paper_id in source_by_id]  # DeepSeek 仅核验已进入目标集合的上游论文。
    query_intent = QueryIntent.model_validate(snapshot.query_intent)  # 从快照冻结的结构化意图恢复生产契约。
    deepseek_result = await deepseek_reranker.rerank(query_intent, input_candidates)  # 唯一可能触发 LLM 的异步边界。
    deepseek_trace = RankingStageTrace(stage="deepseek", enabled=True, input_count=deepseek_result.input_count, output_count=deepseek_result.output_count, candidate_limit=experiment.ranking_config.target_paper_count, latency_ms=deepseek_result.latency_ms, model_name=deepseek_result.model_name, batch_size=None)  # 冻结实际核验规模和模型审计信息。
    final_trace = base_result.stage_traces[-1].model_copy(update={"input_count": deepseek_result.output_count, "output_count": min(deepseek_result.output_count, experiment.ranking_config.target_paper_count)})  # 最终截断以核验后候选为输入。
    papers = [_to_evaluation_paper(paper) for paper in deepseek_result.papers[:experiment.ranking_config.target_paper_count]]  # 保持 DeepSeek 返回顺序并映射到评分契约。
    usage = base_result.prediction.usage.model_copy(update={"llm_calls": 1, "input_tokens": deepseek_result.prompt_tokens, "output_tokens": deepseek_result.completion_tokens, "total_tokens": deepseek_result.prompt_tokens + deepseek_result.completion_tokens, "latency_ms": (base_result.prediction.usage.latency_ms or 0.0) + deepseek_result.latency_ms})  # 保存真实模型用量而不伪造学术 API 增量。
    warnings = [*base_result.prediction.warnings]  # 保留快照与本地阶段已有告警。
    if deepseek_result.ranking_error:  # 部分或全部批次降级必须对报告可见。
        warnings.append(deepseek_result.ranking_error)  # 只加入已净化的生产摘要。
    prediction = base_result.prediction.model_copy(update={"ranking_config": experiment.ranking_config, "papers": papers, "usage": usage, "warnings": warnings})  # 将原配置和核验后输出一并发布。
    return base_result.model_copy(update={"prediction": prediction, "stage_traces": [*base_result.stage_traces[:-1], deepseek_trace, final_trace]})  # 用 DeepSeek 阶段替换原目标前的最终顺序。


def _to_evaluation_paper(paper: object) -> EvaluationPaper:
    """将 CandidatePaper 的公开字段映射为指标模块使用的论文记录。"""
    return EvaluationPaper(paper_id=paper.paper_id, doi=paper.doi, arxiv_id=paper.arxiv_id, pmid=paper.pmid, openalex_id=paper.openalex_id, semantic_scholar_id=paper.semantic_scholar_id, dblp_key=paper.dblp_key, title=paper.title, year=paper.year, authors=list(paper.authors), venue=paper.venue, source=paper.source, url=paper.url, relevance_level=paper.relevance_level, recommendation_reason=paper.recommendation_reason)  # 不推测或重算模型相关性分数。
