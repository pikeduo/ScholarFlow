"""将生产排序前候选结果映射为独立评测候选快照。"""

from datetime import datetime  # 接收调用方冻结的带时区快照生成时间。

from backend.app.models.candidate_generation import CandidateGenerationResult  # 读取规则过滤后、BGE-M3 前的生产内部结果。
from backend.app.models.paper import PaperRecord  # 映射统一论文记录到评测最小候选契约。
from evaluation.contracts.common import EvaluationUsage  # 保存可观测在线候选生成用量且不伪造缺失值。
from evaluation.contracts.snapshot import CandidatePaper, CandidateSnapshot, CandidateSourceRecord, seal_snapshot  # 构造并封存不可变快照。


def build_candidate_snapshot(
    result: CandidateGenerationResult,
    *,
    query_id: str,
    snapshot_id: str,
    latency_ms: float,
    created_at: datetime,
) -> CandidateSnapshot:
    """将一次生产候选生成结果转换为已封存的评测快照。

    参数：
        result：规则过滤后、任何模型排序前的候选生成结果。
        query_id：评测数据集中的稳定查询标识。
        snapshot_id：当前候选集合的唯一快照标识。
        latency_ms：仅候选生成阶段的端到端耗时。
        created_at：包含明确时区的快照创建时间。
    返回：
        CandidateSnapshot：按 RRF 和论文 ID 稳定排序并写入内容哈希的快照。
    异常：
        ValueError：查询缺少独立来源召回规模或结果包含网页发现时抛出。
    """
    query = result.query_intent  # 使用候选服务实际消费的 QueryIntent，避免调用方与执行结果漂移。
    if query.source_recall_count is None:  # 评测快照必须明确区分来源召回规模和最终目标数量。
        raise ValueError("snapshot-export 要求 QueryIntent.source_recall_count 明确设置")  # 禁止使用 target_paper_count 隐式回退。
    if result.route_plan.web_discovery_sources or result.discoveries or result.web_discovery_source_counts:  # 网页发现不属于学术候选快照。
        raise ValueError("snapshot-export 不允许网页发现来源或结果")  # 防止 Tavily 数量和内容进入论文候选契约。
    ranked_papers = sorted(result.papers, key=lambda paper: (-paper.rrf_score, paper.paper_id))  # 复用生产 BGE 跳过路径的稳定 RRF 比较器。
    candidate_papers = [_to_candidate_paper(paper, snapshot_rank=index) for index, paper in enumerate(ranked_papers, start=1)]  # 写入连续一基快照排名。
    frozen_query_intent = query.model_dump(mode="json")  # 保存所有可公开序列化的结构化查询字段。
    frozen_query_intent["retrieval_round"] = query.retrieval_round  # QueryIntent 公共序列化会排除此内部字段，评测快照需显式冻结本轮值。
    warnings = [f"学术来源降级 {source}: {message}" for source, message in result.academic_source_errors.items()]  # 保存不含底层异常的来源错误摘要。
    warnings.extend(f"来源不可用 {source}: {message}" for source, message in result.route_plan.unavailable_reasons.items())  # 保存路由阶段已知配置降级。
    snapshot = CandidateSnapshot(  # 构造尚未写入自引用内容哈希的严格快照。
        snapshot_id=snapshot_id,  # 保存用户显式提供的快照标识。
        query_id=query_id,  # 保存评测数据集查询标识。
        run_id=None,  # 独立单轮导出不创建生产 SearchRunState。
        query=query.normalized_query,  # 使用来源和离线排序共同消费的规范化检索表达。
        query_intent=frozen_query_intent,  # 冻结实际执行意图及单轮标记。
        source_recall_count=query.source_recall_count,  # 保存每来源每轮召回上限。
        target_paper_count=query.target_paper_count,  # 独立保存生产期望最终论文数。
        sources_used=list(result.route_plan.academic_sources),  # 只保存真实执行的学术来源。
        raw_candidate_count=None,  # 当前适配器没有供应商原始响应条目数观测，必须保持空值。
        normalized_candidate_count=result.normalized_candidate_count,  # 保存已映射为 PaperRecord、身份去重前数量。
        deduplicated_candidate_count=result.deduplicated_candidate_count,  # 保存身份去重和 RRF 后、规则过滤前数量。
        filtered_candidate_count=result.filtered_candidate_count,  # 保存确定性规则过滤数量。
        ranking_candidate_count=len(candidate_papers),  # 保存实际进入离线排序的候选数量。
        source_counts=dict(result.academic_source_counts),  # 只保存学术来源成功映射数量。
        filter_reason_counts=dict(result.filter_reason_counts),  # 保存首个失败规则统计。
        papers=candidate_papers,  # 保存稳定 RRF 顺序候选。
        usage=EvaluationUsage(  # 只冻结当前边界能够可靠观测的在线用量。
            academic_api_calls=len(result.route_plan.academic_sources),  # 记录逻辑学术来源调用数，缓存命中仍算一次逻辑调用。
            actual_http_requests=None,  # 共享执行器的重试级 HTTP 总数当前不可可靠观测。
            llm_calls=0,  # 本入口接收现成 QueryIntent 且候选服务不调用 LLM。
            input_tokens=0,  # 本入口没有任何模型输入 Token。
            output_tokens=0,  # 本入口没有任何模型输出 Token。
            total_tokens=0,  # 本入口模型 Token 总数确定为零。
            latency_ms=latency_ms,  # 保存候选生成服务的端到端耗时。
            retry_count=None,  # 当前候选结果未聚合来源重试次数。
            rate_limit_count=None,  # 当前候选结果未聚合 429 次数。
            cache_hit_count=result.cache_hit_count,  # 保存共享来源缓存的有效命中数。
        ),
        stop_reason="candidate_snapshot_ready",  # 标记单轮在线候选已经封存且未继续排序。
        warnings=warnings,  # 保存来源级安全降级信息。
        created_at=created_at,  # 保存带明确时区的创建时间。
    )
    return seal_snapshot(snapshot)  # 返回包含规范化内容 SHA-256 的不可变副本。


def _to_candidate_paper(paper: PaperRecord, *, snapshot_rank: int) -> CandidatePaper:
    """将生产 PaperRecord 映射为不包含模型排序字段的评测候选。"""
    source_records = [  # 仅保留学术来源溯源和原始排名，不携带缓存或认证信息。
        CandidateSourceRecord(source=record.source, external_id=record.external_id, raw_rank=record.raw_rank, matched_subqueries=list(record.matched_subqueries))  # 映射单个来源命中。
        for record in paper.source_records  # 保持融合服务输出的来源记录顺序。
    ]
    return CandidatePaper(  # 丢弃语义、Cross Encoder、LLM 分数和推荐理由等排序后字段。
        paper_id=paper.paper_id,  # 保存快照内稳定论文标识。
        doi=paper.doi,  # 保存最高优先级跨来源身份标识。
        arxiv_id=paper.arxiv_id,  # 保存预印本身份标识。
        pmid=paper.pmid,  # 保存生物医学身份标识。
        openalex_id=paper.openalex_id,  # 保存 OpenAlex 平台标识。
        semantic_scholar_id=paper.semantic_scholar_id,  # 保存 Semantic Scholar 平台标识。
        dblp_key=paper.dblp_key,  # 保存 DBLP 平台标识。
        title=paper.title,  # 保存本地排序必需标题。
        year=paper.year,  # 保存可选发表年份。
        authors=[author.name for author in paper.authors],  # 只保存作者显示名称，不扩散平台作者对象。
        venue=paper.venue,  # 保存可选期刊或会议名称。
        source=paper.source,  # 保存规范记录主来源。
        url=paper.open_access_url,  # 只保留来源明确提供的合法开放访问链接。
        abstract=paper.abstract,  # 保存本地模型可使用的公开摘要。
        keywords=list(paper.keywords),  # 复制来源或规范化关键词。
        source_records=source_records,  # 保存多源命中与原始排名。
        rrf_score=paper.rrf_score,  # 保存模型排序前确定性融合分数。
        snapshot_rank=snapshot_rank,  # 保存按稳定 RRF 顺序生成的一基排名。
    )
