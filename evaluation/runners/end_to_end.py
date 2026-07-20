"""编排用户显式在线执行与完全离线 PaSa 端到端评分。"""

import json  # 读写 UTF-8 JSON 与 JSONL 归档。
import math  # 计算最近秩 P50/P95。
import time  # 记录客户端覆盖 HTTP 往返的端到端耗时。
from pathlib import Path  # 处理用户明确指定的本地路径。
from urllib.error import HTTPError, URLError  # 转换本地服务的安全 HTTP 失败。
from urllib.parse import urlencode  # 编码重复的图谱论文标识参数。
from urllib.request import Request, urlopen  # 仅在用户运行在线命令时访问本地 ScholarFlow 服务。

from evaluation.contracts.common import EvaluationPaper, RelationRecord  # 复用统一论文、关系契约。
from evaluation.contracts.end_to_end import EndToEndEvaluationSummary, EndToEndRunRecord, EndToEndUsage, LlmStageUsage  # 使用本次端到端专用归档。
from evaluation.contracts.gold import GoldQuery  # 读取固定 PaSa GoldQuery。
from evaluation.contracts.prediction import PredictionRecord  # 复用既有离线检索评分输入。
from evaluation.contracts.subset import GoldSubsetManifest  # 校验固定 20 条 manifest。
from evaluation.metrics.aggregate import aggregate_query_metrics  # 复用 Macro/Micro 聚合实现。
from evaluation.metrics.identifiers import deduplicate_papers, has_strong_identifier  # 复用论文去重与标识检查。
from evaluation.metrics.retrieval import evaluate_query  # 复用 P/R/F1@20 计算。
from evaluation.runners.fixture import load_jsonl  # 使用既有严格 UTF-8 JSONL 加载器。


DISCLAIMER = "PaSa AutoScholarQuery dev固定20条初步评测，非完整数据集成绩，非赛事官方成绩"  # 与用户要求保持逐字一致。


def _load_fixed_subset(gold_path: Path, manifest_path: Path) -> list[GoldQuery]:
    """读取并核验固定 PaSa 20 条，拒绝重新抽样或集合漂移。"""
    gold_queries = load_jsonl(gold_path, GoldQuery)  # 只读取用户指定的本地 GoldQuery 文件。
    manifest = GoldSubsetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))  # 严格读取冻结 manifest。
    if manifest.selected_query_count != 20:  # 本次任务固定只允许二十条。
        raise ValueError("本次端到端评测仅接受 selected_query_count=20 的固定 manifest")  # 防止扩大或缩小评测分母。
    gold_by_id = {item.query_id: item for item in gold_queries}  # 通过查询标识恢复 manifest 固定顺序。
    if len(gold_by_id) != len(gold_queries) or set(gold_by_id) != set(manifest.selected_query_ids):  # Gold 与 manifest 必须精确一一对应。
        raise ValueError("GoldQuery 与固定20条 manifest 的 query_id 集合不一致")  # 阻止替换零命中或失败查询。
    return [gold_by_id[query_id] for query_id in manifest.selected_query_ids]  # 以封存顺序返回二十条查询。


def write_execution_plan(gold_path: Path, manifest_path: Path, output_path: Path) -> int:
    """只生成用户手动在线执行所需的固定 20 条计划，不发起任何网络请求。"""
    if output_path.exists():  # 计划是可审计输入，不允许覆盖历史版本。
        raise FileExistsError(f"输出计划已存在: {output_path}")  # 要求用户为新运行选择新路径。
    records = _load_fixed_subset(gold_path, manifest_path)  # 在写出前先核验固定集合边界。
    lines = [json.dumps({"query_id": item.query_id, "query": item.query, "request": {"query": item.query, "search_mode": "standard", "target_paper_count": 20, "enable_semantic_ranking": False, "enable_cross_encoder_ranking": False, "requires_web_evidence": False}}, ensure_ascii=False) for item in records]  # 明确自然语言入口请求，禁止 QueryIntent 直通。
    output_path.parent.mkdir(parents=True, exist_ok=True)  # 仅创建用户指定输出的父目录。
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")  # 写出可供人工审阅的 JSONL 计划。
    return len(records)  # 返回固定查询数供 CLI 输出安全摘要。


def _request_json(url: str, *, method: str = "GET", payload: dict | None = None, timeout_seconds: float = 180.0) -> dict:
    """执行一次评测客户端到本地 API 的请求，并拒绝非对象 JSON 响应。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None  # 仅在 POST 时编码自然语言请求。
    request = Request(url, data=body, method=method, headers={"Content-Type": "application/json"} if body is not None else {})  # 构造不含认证信息的本地 HTTP 请求。
    with urlopen(request, timeout=timeout_seconds) as response:  # 用户显式执行时才访问其已启动的后端。
        data = json.loads(response.read().decode("utf-8"))  # 显式按 UTF-8 解码服务 JSON。
    if not isinstance(data, dict):  # 评测归档要求对象响应以避免字段猜测。
        raise ValueError("服务响应不是 JSON 对象")  # 返回安全、可定位的契约错误。
    return data  # 返回已解析的公共 API 响应。


def _as_paper(payload: dict) -> EvaluationPaper:
    """将生产 PaperRecord 的公共字段投影为离线可评分论文。"""
    authors = [str(item.get("name", "")).strip() for item in payload.get("authors", []) if isinstance(item, dict) and str(item.get("name", "")).strip()]  # 保留作者姓名而不展开无关机构信息。
    return EvaluationPaper(paper_id=payload.get("paper_id"), doi=payload.get("doi"), arxiv_id=payload.get("arxiv_id"), pmid=payload.get("pmid"), openalex_id=payload.get("openalex_id"), semantic_scholar_id=payload.get("semantic_scholar_id"), dblp_key=payload.get("dblp_key"), title=payload.get("title"), year=payload.get("year"), authors=authors, venue=payload.get("venue"), source=payload.get("source"), url=payload.get("open_access_url"), relevance_score=payload.get("llm_relevance_score") or payload.get("cross_encoder_score") or payload.get("semantic_score"), relevance_level=payload.get("constraint_status") if payload.get("constraint_status") in {"high", "partial", "irrelevant", "unknown"} else None, recommendation_reason=payload.get("recommendation_reason"))  # 只使用已保存生产事实，不重新推断字段。


def _safe_error(error: Exception) -> str:
    """将网络或接口异常压缩为不含 URL、查询和堆栈的安全摘要。"""
    if isinstance(error, HTTPError):  # HTTP 状态可帮助离线报告区分服务失败。
        return f"本地搜索接口返回 HTTP {error.code}"  # 不回显响应正文。
    if isinstance(error, URLError):  # 网络层错误不泄露运行机路径。
        return "无法连接本地 ScholarFlow 服务"  # 保持可展示且安全。
    if isinstance(error, TimeoutError):  # 单条超时应保留在二十条分母内。
        return "本地搜索请求超时"  # 返回稳定安全摘要。
    return "本地搜索响应无效"  # 其余异常不向结果泄露内部细节。


def execute_online_plan(plan_path: Path, output_path: Path, *, gold_path: Path, manifest_path: Path, base_url: str, timeout_seconds: float = 180.0) -> int:
    """按计划顺序调用自然语言搜索入口；仅供用户手动显式运行。"""
    if output_path.exists():  # 在线结果是不可覆盖审计记录。
        raise FileExistsError(f"在线结果已存在: {output_path}")  # 防止混合不同配置或时间的结果。
    plan_rows = [json.loads(line) for line in plan_path.read_text(encoding="utf-8").splitlines() if line.strip()]  # 读取已审阅计划而不重新抽样。
    fixed_queries = _load_fixed_subset(gold_path, manifest_path)  # 在连接服务前重新核验冻结二十条集合。
    expected_plan = [{"query_id": item.query_id, "query": item.query, "request": {"query": item.query, "search_mode": "standard", "target_paper_count": 20, "enable_semantic_ranking": False, "enable_cross_encoder_ranking": False, "requires_web_evidence": False}} for item in fixed_queries]  # 重建唯一允许的自然语言请求内容。
    if plan_rows != expected_plan:  # 在线执行不得接受被替换、删减或调整过的计划。
        raise ValueError("在线执行计划与固定 PaSa 20 条自然语言请求不一致")  # 在任何真实调用前阻止集合或配置漂移。
    records: list[EndToEndRunRecord] = []  # 保留计划中的每一条，即使调用失败。
    normalized_base = base_url.rstrip("/")  # 避免路径拼接产生双斜杠。
    for row in plan_rows:  # 严格按冻结计划顺序逐条执行，避免并发放大 API 用量。
        query_id = str(row["query_id"])  # 读取固定稳定查询标识。
        started = time.perf_counter()  # 覆盖评测客户端的自然语言端到端耗时。
        try:  # 失败也必须产出一条 JSONL 记录。
            result = _request_json(f"{normalized_base}/api/v1/search/natural-multi-round", method="POST", payload=row["request"], timeout_seconds=timeout_seconds)  # 强制经过当前完整自然语言入口。
            state = result.get("run_state") if isinstance(result.get("run_state"), dict) else {}  # 读取生产最终运行状态。
            run_id = state.get("run_id") if isinstance(state.get("run_id"), str) else None  # 仅接受有效运行标识。
            papers_payload = result.get("papers") if isinstance(result.get("papers"), list) else []  # 先保留直接响应的论文作为失败回退。
            usage_payload: dict = {}  # 只读 usage 接口成功后写入真实快照统计。
            graph_payload: dict | None = None  # 仅在存在 run_id 与论文标识时请求受限图。
            if run_id:  # 生产已持久化终态时优先读取稳定接口。
                usage_payload = _request_json(f"{normalized_base}/api/v1/usage/{run_id}", timeout_seconds=timeout_seconds)  # 不触发来源或模型调用。
                page = _request_json(f"{normalized_base}/api/v1/search/runs/{run_id}/papers?page=1&page_size=20", timeout_seconds=timeout_seconds)  # 读取同次最终 Top 20 快照。
                papers_payload = page.get("items") if isinstance(page.get("items"), list) else papers_payload  # 以稳定结果接口覆盖临时响应。
                paper_ids = [str(item.get("paper_id")) for item in papers_payload if isinstance(item, dict) and item.get("paper_id")]  # 图接口只接收已保存内部标识。
                if paper_ids:  # 空结果不伪造图。
                    graph_payload = _request_json(f"{normalized_base}/api/v1/graph/citations?{urlencode([('paper_ids', paper_id) for paper_id in paper_ids])}", timeout_seconds=timeout_seconds)  # 仅读取集合内事实关系。
            query_prompt = int(result.get("query_planning_prompt_tokens") or 0)  # 自然入口公开回显 Query Agent 输入 Token。
            query_completion = int(result.get("query_planning_completion_tokens") or 0)  # 自然入口公开回显 Query Agent 输出 Token。
            query_called = 1 if result.get("query_planning_model_name") else 0  # 仅在真实模型名回显时计作实际调用。
            usage = EndToEndUsage(academic_api_calls=usage_payload.get("api_call_count", state.get("api_call_count")), latency_ms=usage_payload.get("latency_ms", state.get("latency_ms", round((time.perf_counter() - started) * 1000))), total_estimated_cost_cny=usage_payload.get("estimated_cost_cny", state.get("estimated_cost_cny")), query_agent=LlmStageUsage(call_count=query_called, input_tokens=query_prompt, output_tokens=query_completion, total_tokens=query_prompt + query_completion), llm_total_tokens=usage_payload.get("token_usage", state.get("token_usage")))  # 未由生产接口暴露的明细保持 None。
            nodes = graph_payload.get("nodes", []) if isinstance(graph_payload, dict) else []  # 仅接受图接口的节点数组。
            edges = graph_payload.get("edges", []) if isinstance(graph_payload, dict) else []  # 仅接受图接口的事实边数组。
            relations = [RelationRecord(source=str(edge["source_paper_id"]), target=str(edge["target_paper_id"]), type=str(edge["edge_type"])) for edge in edges if isinstance(edge, dict) and all(key in edge for key in ("source_paper_id", "target_paper_id", "edge_type"))]  # 严格投影受限图事实边。
            records.append(EndToEndRunRecord(query_id=query_id, run_id=run_id, status=state.get("status") if state.get("status") in {"completed", "failed", "cancelled"} else "invalid_response", papers=[_as_paper(item) for item in papers_payload if isinstance(item, dict)][:20], usage=usage, stop_reason=usage_payload.get("stop_reason", state.get("stop_reason")), degraded_sources=[str(item) for item in state.get("degraded_sources", [])], safe_errors=[str(item) for item in state.get("errors", [])], graph_requested=bool(run_id and papers_payload), graph_generated=isinstance(graph_payload, dict), graph_node_ids=[str(node.get("paper_id")) for node in nodes if isinstance(node, dict) and node.get("paper_id")], relations=relations))  # 保存每条自然语言运行的全部已观测事实。
        except Exception as error:  # 单条失败不得终止余下固定查询。
            status = "timeout" if isinstance(error, TimeoutError) else "transport_error"  # 区分客户端超时和其他连接失败。
            records.append(EndToEndRunRecord(query_id=query_id, status=status, usage=EndToEndUsage(latency_ms=round((time.perf_counter() - started) * 1000)), safe_errors=[_safe_error(error)]))  # 将失败保留在固定分母中。
    output_path.parent.mkdir(parents=True, exist_ok=True)  # 创建用户指定的本地归档目录。
    output_path.write_text("\n".join(record.model_dump_json() for record in records) + "\n", encoding="utf-8")  # 一次性写出全部二十条记录，便于后续离线评分。
    return len(records)  # 返回实际执行记录数供 CLI 显示。


def _percentile(values: list[float], percentile: float) -> float | None:
    """使用最近秩计算小样本可复核分位数。"""
    if not values:  # 无完整耗时样本时保持缺失。
        return None  # 不用零伪造延时。
    return sorted(values)[max(1, math.ceil(len(values) * percentile)) - 1]  # 返回最近秩位置的原始样本值。


def _stage_totals(runs: list[EndToEndRunRecord], stage_name: str) -> dict[str, int | float | None]:
    """汇总某个 LLM 阶段的调用、输入、输出、总 Token 与冻结费用。"""
    stages = [getattr(item.usage, stage_name) for item in runs]  # 按固定二十条顺序读取同名阶段观测。
    complete = lambda field: sum(getattr(item, field) for item in stages) if all(getattr(item, field) is not None for item in stages) else None  # 缺失任一查询时保持整个原始总量未知。
    return {"call_count": complete("call_count"), "input_tokens": complete("input_tokens"), "output_tokens": complete("output_tokens"), "total_tokens": complete("total_tokens"), "estimated_cost_cny": complete("estimated_cost_cny")}  # 返回不混用未知值的阶段原始指标。


def score_end_to_end(gold_path: Path, manifest_path: Path, runs_path: Path, output_dir: Path) -> EndToEndEvaluationSummary:
    """完全离线地评分已归档端到端结果，并输出 JSON、JSONL 和 Markdown。"""
    gold_queries = _load_fixed_subset(gold_path, manifest_path)  # 固定完整二十条分母与顺序。
    runs = load_jsonl(runs_path, EndToEndRunRecord)  # 只读取用户已有在线归档，不访问服务。
    run_by_id = {item.query_id: item for item in runs}  # 构建唯一查询索引供完整性检查。
    expected_ids = [item.query_id for item in gold_queries]  # 读取冻结查询标识顺序。
    if len(run_by_id) != len(runs) or set(run_by_id) != set(expected_ids):  # 不允许丢弃、替换或重复任一查询。
        raise ValueError("在线归档必须恰好覆盖固定 PaSa 20 条，每条仅一次")  # 保证失败仍进入分母。
    predictions = [PredictionRecord(query_id=item.query_id, run_id=item.run_id, papers=item.papers) for item in runs]  # 仅投影论文列表给既有离线检索指标。
    prediction_by_id = {item.query_id: item for item in predictions}  # 建立查询到预测的稳定映射。
    query_metrics = [evaluate_query(gold, prediction_by_id[gold.query_id], [20]) for gold in gold_queries]  # 固定只评测赛题要求的 Top 20。
    retrieval_summary = aggregate_query_metrics(query_metrics, [20])  # 复用既有 Macro 与 Micro 聚合。
    metric_by_id = {item.query_id: item for item in query_metrics}  # 便于计算零命中与至少一篇命中。
    zero_hit_count = sum(metric_by_id[item.query_id].cutoffs[20].true_positive == 0 for item in gold_queries)  # 统计金标命中为零的查询。
    hit_ratio = (len(gold_queries) - zero_hit_count) / len(gold_queries) if gold_queries else 0.0  # 固定分母计算至少命中比例。
    latencies = [float(item.usage.latency_ms) for item in runs if item.usage.latency_ms is not None]  # 收集已记录端到端耗时。
    totals = lambda values: sum(values) if all(value is not None for value in values) else None  # 只有完整二十条观测时才汇总原始计数。
    api_calls = totals([item.usage.academic_api_calls for item in runs])  # 汇总逻辑学术 API 调用。
    http_requests = totals([item.usage.actual_http_requests for item in runs])  # 汇总实际 HTTP 请求或保持缺失。
    retries = totals([item.usage.retry_count for item in runs])  # 汇总重试或保持缺失。
    rate_limits = totals([item.usage.rate_limit_count for item in runs])  # 汇总 429 或保持缺失。
    total_tokens = totals([item.usage.llm_total_tokens for item in runs])  # 汇总已保存运行总 Token。
    total_cost = totals([item.usage.total_estimated_cost_cny for item in runs])  # 汇总冻结人民币费用估算。
    llm_stages = {name: _stage_totals(runs, name) for name in ("query_agent", "query_evolution", "final_verification")}  # 分别输出用户要求的三个 LLM 阶段观测。
    paper_count = sum(len(item.papers) for item in runs)  # 保存全部最终论文总数。
    duplicate_count = sum(deduplicate_papers(item.papers)[1] for item in runs)  # 统计最终列表重复论文。
    field_names = {"title": lambda paper: bool(paper.title and paper.title.strip()), "authors": lambda paper: bool(paper.authors), "year": lambda paper: paper.year is not None, "venue": lambda paper: bool(paper.venue and paper.venue.strip()), "identifier": has_strong_identifier, "recommendation_reason": lambda paper: bool(paper.recommendation_reason and paper.recommendation_reason.strip())}  # 定义赛题要求的字段完整性检查。
    field_completeness = {name: (sum(check(paper) for item in runs for paper in item.papers) / paper_count if paper_count else 0.0) for name, check in field_names.items()}  # 按真实最终论文分母计算完整率。
    graph_generated_count = sum(item.graph_generated for item in runs)  # 统计成功取得受限图的查询数。
    dangling_edges = sum(edge.source not in set(item.graph_node_ids) or edge.target not in set(item.graph_node_ids) for item in runs for edge in item.relations)  # 图边端点必须属于图节点。
    unsupported_edges = sum(edge.type not in {"cites", "same_work"} for item in runs for edge in item.relations)  # 只允许生产图接口的两类事实关系。
    missing = [name for name, value in {"actual_http_requests": http_requests, "retry_count": retries, "rate_limit_count": rate_limits}.items() if value is None]  # 列出生产当前未暴露的必要原始指标。
    summary = EndToEndEvaluationSummary(query_count=len(gold_queries), retrieval={"precision_at_20": retrieval_summary.cutoffs[20].model_dump(), "zero_hit_query_count": zero_hit_count, "at_least_one_hit_ratio": hit_ratio, "completed_query_count": sum(item.status == "completed" for item in runs), "failed_or_timeout_query_count": sum(item.status != "completed" for item in runs)}, efficiency={"academic_api_logical_calls": api_calls, "actual_http_requests": http_requests, "retry_count": retries, "rate_limit_count": rate_limits, "llm_stages": llm_stages, "llm_total_tokens": total_tokens, "latency_mean_ms": sum(latencies) / len(latencies) if len(latencies) == len(runs) and latencies else None, "latency_p50_ms": _percentile(latencies, 0.5) if len(latencies) == len(runs) else None, "latency_p95_ms": _percentile(latencies, 0.95) if len(latencies) == len(runs) else None, "latency_max_ms": max(latencies) if len(latencies) == len(runs) and latencies else None, "total_estimated_cost_cny": total_cost, "average_cost_cny": total_cost / len(runs) if total_cost is not None and runs else None, "missing_fields": missing}, structure={"final_paper_count": paper_count, "duplicate_paper_count": duplicate_count, "field_completeness": field_completeness, "graph_generated_query_count": graph_generated_count, "graph_generated_ratio": graph_generated_count / len(runs) if runs else 0.0, "graph_node_count": sum(len(item.graph_node_ids) for item in runs), "graph_edge_count": sum(len(item.relations) for item in runs), "dangling_edge_count": dangling_edges, "unsupported_relation_count": unsupported_edges}, warnings=["赛题尚未公开效率与结构化指标的归一化公式；本报告以原始指标为主体，未生成或宣称官方分数。", *([f"生产只读快照当前未提供：{', '.join(missing)}；报告保持 N/A，未按零计入。"] if missing else [])])  # 形成可复核、非官方的初步报告。
    output_dir.mkdir(parents=True, exist_ok=True)  # 创建明确的离线报告目录。
    (output_dir / "report.json").write_text(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")  # 写出机读汇总 JSON。
    (output_dir / "query_metrics.jsonl").write_text("\n".join(json.dumps({"query_id": item.query_id, "status": run_by_id[item.query_id].status, **item.model_dump(mode="json")}, ensure_ascii=False) for item in query_metrics) + "\n", encoding="utf-8")  # 写出二十条查询级评分与状态。
    metrics = retrieval_summary.cutoffs[20]  # 读取唯一 Top 20 聚合指标。
    markdown = "\n".join(["# ScholarFlow PaSa 端到端初步评测报告", "", f"> {DISCLAIMER}", "", "## F1 Score（70%）", "", f"- Precision@20：Macro {metrics.macro_precision:.4f}；Micro {metrics.micro_precision:.4f}", f"- Recall@20：Macro {metrics.macro_recall:.4f}；Micro {metrics.micro_recall:.4f}", f"- F1@20：Macro {metrics.macro_f1:.4f}；Micro {metrics.micro_f1:.4f}", f"- 零命中查询数：{zero_hit_count}/{len(gold_queries)}", f"- 至少命中一篇论文的查询比例：{hit_ratio:.2%}", "", "## 运行效率（20%，原始指标）", "", f"- 学术 API 逻辑调用次数：{api_calls if api_calls is not None else 'N/A'}", f"- 实际 HTTP 请求次数：{http_requests if http_requests is not None else 'N/A'}", f"- 重试次数 / 429 次数：{retries if retries is not None else 'N/A'} / {rate_limits if rate_limits is not None else 'N/A'}", f"- Query Agent 调用 / 输入 / 输出 Token：{llm_stages['query_agent']['call_count'] if llm_stages['query_agent']['call_count'] is not None else 'N/A'} / {llm_stages['query_agent']['input_tokens'] if llm_stages['query_agent']['input_tokens'] is not None else 'N/A'} / {llm_stages['query_agent']['output_tokens'] if llm_stages['query_agent']['output_tokens'] is not None else 'N/A'}", f"- 查询演化与最终核验 LLM 明细：{json.dumps({'query_evolution': llm_stages['query_evolution'], 'final_verification': llm_stages['final_verification']}, ensure_ascii=False)}", f"- LLM 总 Token：{total_tokens if total_tokens is not None else 'N/A'}", f"- 平均 / P50 / P95 / 最大延时（ms）：{summary.efficiency['latency_mean_ms'] if summary.efficiency['latency_mean_ms'] is not None else 'N/A'} / {summary.efficiency['latency_p50_ms'] if summary.efficiency['latency_p50_ms'] is not None else 'N/A'} / {summary.efficiency['latency_p95_ms'] if summary.efficiency['latency_p95_ms'] is not None else 'N/A'} / {summary.efficiency['latency_max_ms'] if summary.efficiency['latency_max_ms'] is not None else 'N/A'}", f"- 总费用 / 平均每查询费用（CNY）：{total_cost if total_cost is not None else 'N/A'} / {summary.efficiency['average_cost_cny'] if summary.efficiency['average_cost_cny'] is not None else 'N/A'}", "", "## 回复结果结构化（10%，原始指标）", "", f"- 最终论文数 / 重复论文数：{paper_count} / {duplicate_count}", f"- 字段完整率：{', '.join(f'{name}={value:.2%}' for name, value in field_completeness.items())}", f"- 关系图生成：{graph_generated_count}/{len(runs)}；节点 / 边：{summary.structure['graph_node_count']} / {summary.structure['graph_edge_count']}", f"- 悬空边 / 无依据关系：{dangling_edges} / {unsupported_edges}", "", "## 说明", "", *[f"- {warning}" for warning in summary.warnings], ""])  # 生成人工可审阅 Markdown，明确非官方边界。
    (output_dir / "report.md").write_text(markdown, encoding="utf-8")  # 写出初步评测 Markdown 报告。
    return summary  # 返回汇总供 CLI 与离线测试使用。
