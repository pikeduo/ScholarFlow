"""基于同一候选快照执行可注入打分器的完全离线排序消融。"""

import json  # 读取矩阵配置并写出不执行模型的计划文件。
import math  # 拒绝 NaN 和无穷模型分数。
from copy import deepcopy  # 隔离打分器对查询意图和候选对象的潜在修改。
from dataclasses import dataclass  # 保存运行器内部候选及阶段分数。
from pathlib import Path  # 处理用户显式提供的本地矩阵与计划路径。

from evaluation.adapters.base import OfflineRankingScorer  # 依赖不绑定模型库的打分协议。
from evaluation.contracts.ablation import AblationExperiment, AblationMatrix, AblationPlan, OfflineAblationResult, RankingScoreBatch, RankingStageTrace  # 使用消融输入、计划和结果契约。
from evaluation.contracts.common import EvaluationPaper, EvaluationUsage  # 构造第一阶段可评分预测与 usage。
from evaluation.contracts.prediction import PredictionRecord  # 输出兼容既有指标模块的预测。
from evaluation.contracts.snapshot import CandidatePaper, CandidateSnapshot, compute_snapshot_hash  # 读取不可变排序前快照。
from evaluation.runners.snapshot_loader import validate_snapshot_integrity  # 在运行前后核验快照未被改变。


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    """保存运行器内部论文及可选语义、精排分数。"""

    paper: CandidatePaper  # 保存深拷贝后的候选论文。
    semantic_score: float | None = None  # 保存当前配置的 BGE-M3 分数。
    cross_encoder_score: float | None = None  # 保存当前配置的 Cross Encoder 分数。


def load_ablation_matrix(path: Path) -> AblationMatrix:
    """以 UTF-8 只读加载本地消融矩阵 JSON。"""
    with path.open("r", encoding="utf-8") as stream:  # 不读取环境变量或生产配置。
        payload = json.load(stream)  # 解析单个矩阵对象。
    return AblationMatrix.model_validate(payload)  # 校验共享召回、评分口径和零 DeepSeek 边界。


def write_ablation_plan(plan: AblationPlan, path: Path) -> None:
    """将零 API、零 DeepSeek 的离线任务计划写入用户指定 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)  # 只创建显式输出文件的父目录。
    payload = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"  # 生成可审阅 UTF-8 JSON。
    path.write_text(payload, encoding="utf-8")  # 写出计划而不执行任何打分器。


def build_ablation_plan(snapshots: list[CandidateSnapshot], matrix: AblationMatrix) -> AblationPlan:
    """验证所有配置复用输入快照并生成零 API、零 DeepSeek 的任务计划。"""
    if not snapshots:  # 内存调用同样不能生成空计划。
        raise ValueError("消融计划至少需要一份候选快照")  # 防止零任务被误认为评测成功。
    snapshot_hashes: dict[str, str] = {}  # 保存每份快照实际摘要。
    expected_source_count = matrix.experiments[0].ranking_config.source_recall_count  # 矩阵契约已保证所有配置共享该值。
    if len({snapshot.snapshot_id for snapshot in snapshots}) != len(snapshots):  # 直接传入内存列表时同样拒绝重复标识。
        raise ValueError("消融计划包含重复 snapshot_id")  # 防止结果归档覆盖。
    if len({snapshot.query_id for snapshot in snapshots}) != len(snapshots):  # 每条查询只允许一份共享在线快照。
        raise ValueError("消融计划包含重复 query_id")  # 防止不同候选集进入同一比较。
    for snapshot in snapshots:  # 逐份冻结离线输入。
        actual_hash = validate_snapshot_integrity(snapshot)  # 要求快照已封存且未被修改。
        if snapshot.source_recall_count != expected_source_count:  # 在线召回规模变化需要新快照而非复用。
            raise ValueError(f"快照 {snapshot.snapshot_id} 的 source_recall_count 与消融矩阵不一致")  # 阻止错误横向比较。
        snapshot_hashes[snapshot.snapshot_id] = actual_hash  # 保存计划实际校验的内容哈希。
    return AblationPlan(matrix_id=matrix.matrix_id, snapshot_ids=[snapshot.snapshot_id for snapshot in snapshots], snapshot_hashes=snapshot_hashes, experiment_ids=[experiment.experiment_id for experiment in matrix.experiments], task_count=len(snapshots) * len(matrix.experiments))  # 返回不执行模型的计划。


def _validate_score_batch(batch: RankingScoreBatch, candidates: list[_RankedCandidate], stage_name: str) -> None:
    """要求模型分数与输入一一对应且全部为有限数。"""
    if len(batch.scores) != len(candidates):  # 长度不一致会把分数写给错误论文。
        raise ValueError(f"{stage_name} 分数数量与输入候选数量不一致")  # 拒绝不可解释输出。
    if any(not math.isfinite(score) for score in batch.scores):  # NaN 或无穷会破坏稳定排序。
        raise ValueError(f"{stage_name} 分数必须全部为有限数")  # 返回清晰适配器错误。


def _run_semantic_stage(candidates: list[_RankedCandidate], snapshot: CandidateSnapshot, scorer: OfflineRankingScorer, candidate_limit: int) -> tuple[list[_RankedCandidate], RankingStageTrace]:
    """执行 BGE-M3 打分、稳定排序和参数化截断。"""
    scorer_papers = tuple(candidate.paper.model_copy(deep=True) for candidate in candidates)  # 为打分器再复制一层，保护运行器内部排序对象。
    batch = scorer.score(snapshot.query, deepcopy(snapshot.query_intent), scorer_papers)  # 隔离打分器对 QueryIntent 和候选的修改。
    _validate_score_batch(batch, candidates, "BGE-M3")  # 在绑定论文前校验分数形状。
    scored = [_RankedCandidate(paper=candidate.paper, semantic_score=float(score)) for candidate, score in zip(candidates, batch.scores, strict=True)]  # 按输入位置绑定语义分数。
    ranked = sorted(scored, key=lambda candidate: (-float(candidate.semantic_score), -candidate.paper.rrf_score, candidate.paper.paper_id))  # 使用 RRF 和 ID 稳定打破同分。
    retained = ranked[:candidate_limit]  # 按配置保留 BGE-M3 候选。
    trace = RankingStageTrace(stage="bge_m3", enabled=True, input_count=len(candidates), output_count=len(retained), candidate_limit=candidate_limit, latency_ms=batch.latency_ms, model_name=batch.model_name, device=batch.device, batch_size=batch.batch_size, oom_retry_count=batch.oom_retry_count)  # 固化阶段统计。
    return retained, trace  # 返回语义候选和审计信息。


def _run_cross_encoder_stage(candidates: list[_RankedCandidate], snapshot: CandidateSnapshot, scorer: OfflineRankingScorer, candidate_limit: int) -> tuple[list[_RankedCandidate], RankingStageTrace]:
    """执行 Cross Encoder 打分、稳定排序和参数化截断。"""
    scorer_papers = tuple(candidate.paper.model_copy(deep=True) for candidate in candidates)  # 为打分器再复制一层，保护上游分数绑定对象。
    batch = scorer.score(snapshot.query, deepcopy(snapshot.query_intent), scorer_papers)  # Cross-only 读取完整快照，组合配置读取 BGE 输出。
    _validate_score_batch(batch, candidates, "Cross Encoder")  # 在绑定论文前校验分数形状。
    scored = [_RankedCandidate(paper=candidate.paper, semantic_score=candidate.semantic_score, cross_encoder_score=float(score)) for candidate, score in zip(candidates, batch.scores, strict=True)]  # 保留可用语义分数作为次级键。
    ranked = sorted(scored, key=lambda candidate: (-float(candidate.cross_encoder_score), -_score_or_negative_infinity(candidate.semantic_score), -candidate.paper.rrf_score, candidate.paper.paper_id))  # 使用 CE、BGE、RRF 和 ID 稳定排序。
    retained = ranked[:candidate_limit]  # 按配置保留精排候选。
    trace = RankingStageTrace(stage="cross_encoder", enabled=True, input_count=len(candidates), output_count=len(retained), candidate_limit=candidate_limit, latency_ms=batch.latency_ms, model_name=batch.model_name, device=batch.device, batch_size=batch.batch_size, oom_retry_count=batch.oom_retry_count)  # 固化阶段统计。
    return retained, trace  # 返回精排候选和审计信息。


def _score_or_negative_infinity(score: float | None) -> float:
    """将缺失上游分数转换为负无穷供稳定排序。"""
    return score if score is not None else float("-inf")  # Cross-only 配置会自然回退 RRF 次级键。


def _to_evaluation_paper(candidate: _RankedCandidate) -> EvaluationPaper:
    """将候选映射为第一阶段指标与报告兼容的预测论文。"""
    paper = candidate.paper  # 缩短字段映射表达式。
    return EvaluationPaper(paper_id=paper.paper_id, doi=paper.doi, arxiv_id=paper.arxiv_id, pmid=paper.pmid, openalex_id=paper.openalex_id, semantic_scholar_id=paper.semantic_scholar_id, dblp_key=paper.dblp_key, title=paper.title, year=paper.year, authors=list(paper.authors), venue=paper.venue, source=paper.source, url=paper.url, relevance_level=paper.relevance_level, recommendation_reason=paper.recommendation_reason)  # 不把范围不确定的模型原始分数伪装为零至一相关性分数。


def _build_usage(snapshot: CandidateSnapshot, traces: list[RankingStageTrace]) -> EvaluationUsage:
    """合并冻结在线 usage 与本次离线模型阶段观测。"""
    semantic_trace = next((trace for trace in traces if trace.stage == "bge_m3"), None)  # 定位语义阶段。
    cross_trace = next((trace for trace in traces if trace.stage == "cross_encoder"), None)  # 定位精排阶段。
    local_latency = sum(trace.latency_ms or 0.0 for trace in (semantic_trace, cross_trace) if trace is not None and trace.enabled)  # 汇总实际执行的本地模型耗时。
    total_latency = snapshot.usage.latency_ms + local_latency if snapshot.usage.latency_ms is not None else None  # 只有在线耗时存在时才形成完整端到端耗时。
    devices = [f"{trace.stage}={trace.device}" for trace in (semantic_trace, cross_trace) if trace is not None and trace.enabled and trace.device]  # 保存各阶段设备而不丢失差异。
    batches = [trace.batch_size for trace in (semantic_trace, cross_trace) if trace is not None and trace.enabled and trace.batch_size is not None]  # 收集实际批大小。
    shared_batch_size = batches[0] if batches and all(batch == batches[0] for batch in batches) else None  # 仅在各阶段相同时写入共享字段。
    return snapshot.usage.model_copy(update={"latency_ms": total_latency, "bge_input_count": semantic_trace.input_count if semantic_trace and semantic_trace.enabled else 0, "bge_output_count": semantic_trace.output_count if semantic_trace and semantic_trace.enabled else 0, "bge_latency_ms": semantic_trace.latency_ms if semantic_trace and semantic_trace.enabled else 0.0, "cross_encoder_input_count": cross_trace.input_count if cross_trace and cross_trace.enabled else 0, "cross_encoder_output_count": cross_trace.output_count if cross_trace and cross_trace.enabled else 0, "cross_encoder_latency_ms": cross_trace.latency_ms if cross_trace and cross_trace.enabled else 0.0, "local_model_device": ";".join(devices) or None, "batch_size": shared_batch_size, "oom_retry_count": sum(trace.oom_retry_count or 0 for trace in (semantic_trace, cross_trace) if trace is not None and trace.enabled)})  # 明确禁用阶段为已观测零执行。


def run_offline_experiment(snapshot: CandidateSnapshot, matrix_id: str, experiment: AblationExperiment, *, semantic_scorer: OfflineRankingScorer | None = None, cross_encoder_scorer: OfflineRankingScorer | None = None) -> OfflineAblationResult:
    """在一份快照上执行一个配置，不实例化模型或访问生产搜索。"""
    before_hash = validate_snapshot_integrity(snapshot)  # 运行前确认输入快照未被修改。
    config = experiment.ranking_config  # 读取已通过 DeepSeek 禁用校验的排序配置。
    if config.source_recall_count != snapshot.source_recall_count:  # 单实验也必须保持在线召回规模一致。
        raise ValueError("ranking_config.source_recall_count 与候选快照不一致")  # 防止复用错误快照。
    if config.semantic_ranking_enabled and semantic_scorer is None:  # 启用 BGE-M3 时必须由用户显式注入适配器。
        raise ValueError("配置启用 BGE-M3，但未提供离线 semantic_scorer")  # 不自动加载或下载模型。
    if config.cross_encoder_ranking_enabled and cross_encoder_scorer is None:  # 启用 CE 时必须由用户显式注入适配器。
        raise ValueError("配置启用 Cross Encoder，但未提供离线 cross_encoder_scorer")  # 不自动加载或下载模型。
    candidates = [_RankedCandidate(paper=paper.model_copy(deep=True)) for paper in snapshot.papers]  # 每个配置从同一快照独立深拷贝开始。
    traces = [RankingStageTrace(stage="rrf", enabled=True, input_count=len(candidates), output_count=len(candidates))]  # 记录共享基线候选数。
    if config.semantic_ranking_enabled and semantic_scorer is not None:  # 仅在配置和依赖同时明确启用时执行 BGE-M3。
        candidates, trace = _run_semantic_stage(candidates, snapshot, semantic_scorer, config.semantic_top_k)  # 执行可配置粗排。
        traces.append(trace)  # 保存模型统计。
    else:  # 禁用阶段不得截断或调用打分器。
        traces.append(RankingStageTrace(stage="bge_m3", enabled=False, input_count=len(candidates), output_count=len(candidates), candidate_limit=config.semantic_top_k))  # 明确记录主动跳过。
    if config.cross_encoder_ranking_enabled and cross_encoder_scorer is not None:  # 仅在配置和依赖同时明确启用时执行 CE。
        candidates, trace = _run_cross_encoder_stage(candidates, snapshot, cross_encoder_scorer, config.cross_encoder_top_k)  # 执行可配置精排。
        traces.append(trace)  # 保存模型统计。
    else:  # 禁用阶段不得截断或调用打分器。
        traces.append(RankingStageTrace(stage="cross_encoder", enabled=False, input_count=len(candidates), output_count=len(candidates), candidate_limit=config.cross_encoder_top_k))  # 明确记录主动跳过。
    final_candidates = candidates[:config.target_paper_count]  # 最终目标数量独立于评分 Top-K。
    traces.append(RankingStageTrace(stage="target", enabled=True, input_count=len(candidates), output_count=len(final_candidates), candidate_limit=config.target_paper_count))  # 固化最终截断统计。
    prediction = PredictionRecord(query_id=snapshot.query_id, snapshot_id=snapshot.snapshot_id, run_id=snapshot.run_id, ranking_config=config, papers=[_to_evaluation_paper(candidate) for candidate in final_candidates], usage=_build_usage(snapshot, traces), warnings=list(snapshot.warnings))  # 构造第一阶段可直接评分的预测。
    after_hash = compute_snapshot_hash(snapshot)  # 运行后重新计算原始对象摘要。
    if before_hash != after_hash:  # 任一打分器若修改快照共享对象都必须失败。
        raise RuntimeError("离线排序过程中候选快照被修改")  # 保护后续配置的公平性。
    return OfflineAblationResult(matrix_id=matrix_id, experiment_id=experiment.experiment_id, snapshot_id=snapshot.snapshot_id, snapshot_hash=before_hash, query_id=snapshot.query_id, prediction=prediction, stage_traces=traces)  # 返回可审计结果。


def run_ablation_matrix(snapshots: list[CandidateSnapshot], matrix: AblationMatrix, *, semantic_scorer: OfflineRankingScorer | None = None, cross_encoder_scorer: OfflineRankingScorer | None = None) -> list[OfflineAblationResult]:
    """按快照和配置稳定顺序执行矩阵，所有任务复用已校验快照。"""
    build_ablation_plan(snapshots, matrix)  # 在任何本地打分前验证整批快照与矩阵。
    return [run_offline_experiment(snapshot, matrix.matrix_id, experiment, semantic_scorer=semantic_scorer, cross_encoder_scorer=cross_encoder_scorer) for snapshot in snapshots for experiment in matrix.experiments]  # 返回快照优先、配置次优先的稳定结果。
