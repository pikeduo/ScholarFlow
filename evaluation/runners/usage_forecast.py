"""为评测真实调用生成不访问网络的资源预估。"""

import hashlib  # 固定输入与预估内容的可确认哈希。
import json  # 读写可审阅且不含查询正文的 JSON。
from math import ceil  # 用保守字符比例估算 Token 上限。
from pathlib import Path  # 处理用户明确指定的本地输入和输出。

from backend.app.core.deepseek_pricing import estimate_deepseek_cost_or_zero  # 复用已冻结的本地价格计算。
from backend.app.models.query_intent import QueryIntent  # 校验快照预估所需的生产查询契约。
from evaluation.runners.offline_ranking import load_ablation_matrix  # 读取用户审核的 DeepSeek 实验配置。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 只读加载封存候选而不访问来源。


def forecast_query_agent(*, input_manifest_path: Path, query_ids: list[str], output_path: Path, model_name: str = "deepseek-v4-flash") -> dict[str, object]:
    """估算一次或多次评测 Query Agent 调用的 Token、费用与次数。"""
    manifest = _load_manifest(input_manifest_path)  # 只读取 QueryIntent 映射，不读取 Gold 或候选。
    selected_ids = _select_ids(manifest, query_ids)  # 按冻结顺序确定本次实际调用范围。
    prompt_tokens = 0  # 汇总保守输入 Token 上限。
    for query_id in selected_ids:  # 逐条读取唯一允许进入 Query Agent 的原始问题。
        intent = QueryIntent.model_validate_json(Path(manifest["query_intent_files"][query_id]).read_text(encoding="utf-8"))  # 只校验 QueryIntent。
        prompt_tokens += 900 + ceil(len(intent.original_query.encode("utf-8")) / 3)  # 包含固定系统提示与 UTF-8 问题的保守上界。
    completion_tokens = 3000 * len(selected_ids)  # 与生产 Query Agent 的单次最大输出保持一致。
    cost = estimate_deepseek_cost_or_zero(model_name, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=prompt_tokens)  # 缓存未知时按未命中保守计费。
    payload = {"schema_version": "evaluation-usage-forecast-v1", "operation": "query-agent-plan", "input_sha256": _sha256(input_manifest_path), "query_id_order": selected_ids, "deepseek_calls": len(selected_ids), "academic_api_calls": 0, "estimated_prompt_tokens_upper_bound": prompt_tokens, "estimated_completion_tokens_upper_bound": completion_tokens, "estimated_total_tokens_upper_bound": prompt_tokens + completion_tokens, "estimated_cost_cny_upper_bound": cost.cost_cny, "peak_pricing_applied": cost.peak_pricing_applied, "assumptions": ["输入 Token 按 UTF-8 字节数除以 3 加固定系统提示估算", "输出按每次 3000 Token 上限估算", "不读取 Gold、候选快照、报告或 .env"]}  # 仅写入可审计的聚合值。
    return _write_forecast(payload, output_path)  # 发布新文件并返回带确认哈希的完整预估。


def forecast_snapshot_export(*, query_intent_path: Path, query_id: str, snapshot_id: str, output_path: Path) -> dict[str, object]:
    """估算一次第一轮候选快照导出的学术 API 调用上限。"""
    intent = QueryIntent.model_validate_json(query_intent_path.read_text(encoding="utf-8"))  # 只读取用户指定的 QueryIntent。
    if intent.retrieval_round != 1 or intent.source_recall_count is None:  # 预估必须与单轮快照导出边界一致。
        raise ValueError("快照预估要求第一轮且明确设置 source_recall_count")  # 不为无法执行的调用生成确认文件。
    payload = {"schema_version": "evaluation-usage-forecast-v1", "operation": "snapshot-export", "input_sha256": _sha256(query_intent_path), "query_id_order": [query_id], "snapshot_id": snapshot_id, "source_recall_count": intent.source_recall_count, "deepseek_calls": 0, "academic_api_calls": 1, "actual_http_request_upper_bound": 4, "estimated_prompt_tokens_upper_bound": 0, "estimated_completion_tokens_upper_bound": 0, "estimated_total_tokens_upper_bound": 0, "estimated_cost_cny_upper_bound": 0.0, "assumptions": ["第一轮动态路由当前计划一个学术来源", "HTTP 上限按一次初始请求加默认最多三次重试估算", "缓存命中、429 冷却和实际重试不可在调用前精确预测"]}  # 明确逻辑调用与物理尝试不是同一指标。
    return _write_forecast(payload, output_path)  # 发布不可覆盖的确认预估。


def forecast_deepseek_ablation(*, snapshots_path: Path, matrix_path: Path, plan_path: Path, experiment_ids: list[str], output_path: Path, model_name: str = "deepseek-v4-flash", batch_size: int = 10) -> dict[str, object]:
    """按封存候选和启用实验估算 DeepSeek 核验调用上限。"""
    if batch_size < 1:  # 零批大小无法估算请求次数。
        raise ValueError("DeepSeek 预估 batch_size 必须大于零")  # 拒绝无效命令参数。
    snapshots = load_candidate_snapshots(snapshots_path)  # 只读校验共享候选。
    matrix = load_ablation_matrix(matrix_path)  # 只读加载矩阵而不构造模型。
    requested = {item.strip() for item in experiment_ids if item.strip()}  # 规范化用户选择。
    experiments = [item for item in matrix.experiments if item.experiment_id in requested and item.ranking_config.deepseek_enabled]  # 仅统计实际启用 LLM 的实验。
    if not experiments or len(requested) != len(experiments):  # 禁止把未启用或未知实验误当作 LLM 调用。
        raise ValueError("DeepSeek 预估要求全部选中实验均启用 deepseek_enabled")  # 保持预估与执行范围一一对应。
    calls = 0  # 累计理论最大批次调用数。
    prompt_tokens = 0  # 累计保守输入 Token 上限。
    completion_tokens = 0  # 累计每批最大结构化输出 Token。
    for snapshot in snapshots:  # 每份快照在每个实验中独立核验。
        for experiment in experiments:  # 深度实验不得共享模型调用或结果。
            candidate_count = min(len(snapshot.papers), experiment.ranking_config.target_paper_count)  # 与异步执行器当前目标集合边界一致。
            batch_count = ceil(candidate_count / batch_size)  # 空候选自然产生零调用。
            calls += batch_count  # 累计计划调用数。
            prompt_tokens += 700 + ceil(len(snapshot.query.encode("utf-8")) / 3) + sum(ceil((len(paper.title) + len(paper.abstract or "")) / 3) for paper in snapshot.papers[:candidate_count])  # 按冻结标题摘要和固定提示保守估算。
            completion_tokens += batch_count * 4000  # 使用生产单批最大输出 Token 上限。
    cost = estimate_deepseek_cost_or_zero(model_name, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=prompt_tokens)  # 缓存未知时按未命中保守估算。
    payload = {"schema_version": "evaluation-usage-forecast-v1", "operation": "ablation-deepseek", "snapshots_sha256": _sha256(snapshots_path), "matrix_sha256": _sha256(matrix_path), "plan_sha256": _sha256(plan_path), "experiment_ids": [item.experiment_id for item in experiments], "deepseek_calls": calls, "academic_api_calls": 0, "estimated_prompt_tokens_upper_bound": prompt_tokens, "estimated_completion_tokens_upper_bound": completion_tokens, "estimated_total_tokens_upper_bound": prompt_tokens + completion_tokens, "estimated_cost_cny_upper_bound": cost.cost_cny, "peak_pricing_applied": cost.peak_pricing_applied, "assumptions": ["每个实验和快照独立核验", "每批最多 10 篇、4,000 输出 Token", "只读取封存快照，不调用学术 API或本地模型"]}  # 写入范围与公式而不泄漏查询正文。
    return _write_forecast(payload, output_path)  # 发布不可覆盖的预估确认文件。


def validate_approved_forecast(*, forecast_path: Path, confirmation_sha256: str, operation: str, input_path: Path, query_ids: list[str], snapshot_id: str | None = None) -> None:
    """确认预估未被篡改且与即将执行的真实调用输入完全一致。"""
    payload = json.loads(forecast_path.read_text(encoding="utf-8"))  # 只读取用户显式提供的已审阅预估。
    if not isinstance(payload, dict) or payload.get("schema_version") != "evaluation-usage-forecast-v1":  # 拒绝任意 JSON 伪装成预估。
        raise ValueError("调用前预估文件格式无效")  # 在客户端创建前返回稳定错误。
    recorded_confirmation = payload.pop("confirmation_sha256", None)  # 将确认值与其余冻结内容分离。
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))  # 按写入时同一规则重建哈希材料。
    expected_confirmation = hashlib.sha256(normalized.encode("utf-8")).hexdigest()  # 计算不可伪造的内容确认值。
    if recorded_confirmation != expected_confirmation or confirmation_sha256 != expected_confirmation:  # 用户确认值和文件内容必须同时一致。
        raise ValueError("调用前预估确认 SHA-256 不匹配")  # 阻止过期、篡改或未审阅预估。
    if payload.get("operation") != operation or payload.get("input_sha256") != _sha256(input_path):  # 输入文件变更后必须重新预估。
        raise ValueError("调用前预估与当前操作或输入不一致")  # 防止复用其他查询的预估。
    if payload.get("query_id_order") != _expected_query_ids(operation, input_path, query_ids):  # 绑定稳定查询范围而非仅校验数量。
        raise ValueError("调用前预估与当前 query_id 不一致")  # 禁止减少或扩大调用范围。
    if operation == "snapshot-export" and payload.get("snapshot_id") != snapshot_id:  # 快照标识也属于线上审计范围。
        raise ValueError("调用前预估与当前 snapshot_id 不一致")  # 防止同一查询复用到另一输出实验。


def validate_deepseek_ablation_forecast(*, forecast_path: Path, confirmation_sha256: str, snapshots_path: Path, matrix_path: Path, plan_path: Path, experiment_ids: list[str]) -> None:
    """校验 DeepSeek 预估与本次消融全部冻结输入一致。"""
    payload = json.loads(forecast_path.read_text(encoding="utf-8"))  # 只读用户确认过的本地预估。
    if not isinstance(payload, dict):  # 任意 JSON 不能充当调用许可。
        raise ValueError("DeepSeek 预估文件无效")  # 在客户端创建前失败。
    recorded = payload.pop("confirmation_sha256", None)  # 从哈希材料中移除确认字段。
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))  # 复现生成时的规范序列化。
    if recorded != confirmation_sha256 or hashlib.sha256(normalized.encode("utf-8")).hexdigest() != confirmation_sha256:  # 用户确认值和文件内容必须同时匹配。
        raise ValueError("DeepSeek 预估确认 SHA-256 不匹配")  # 禁止篡改或过期文件。
    if payload.get("operation") != "ablation-deepseek" or payload.get("snapshots_sha256") != _sha256(snapshots_path) or payload.get("matrix_sha256") != _sha256(matrix_path) or payload.get("plan_sha256") != _sha256(plan_path) or payload.get("experiment_ids") != [item.strip() for item in experiment_ids]:  # 绑定所有输入与实验顺序。
        raise ValueError("DeepSeek 预估与当前消融输入不一致")  # 不允许跨集合复用预估。


def _expected_query_ids(operation: str, input_path: Path, query_ids: list[str]) -> list[str]:
    """按操作类型生成与预估文件相同的稳定查询顺序。"""
    if operation == "query-agent-plan":  # Query Agent 必须恢复 manifest 的冻结顺序。
        return _select_ids(_load_manifest(input_path), query_ids)  # 复用预估时的相同选择逻辑。
    return [query_ids[0]]  # 单快照导出只允许一个查询标识。


def _load_manifest(path: Path) -> dict[str, object]:
    """加载最小 QueryIntent manifest，并拒绝其他输入格式。"""
    payload = json.loads(path.read_text(encoding="utf-8"))  # 只读取用户显式提供的本地文件。
    if not isinstance(payload, dict) or payload.get("schema_version") != "query-intent-manifest-v1":  # 防止 Gold 或候选被误用。
        raise ValueError("Query Agent 预估要求 query-intent-manifest-v1")  # 保持输入边界与执行器一致。
    return payload  # 由后续选择逻辑校验必要映射字段。


def _select_ids(manifest: dict[str, object], query_ids: list[str]) -> list[str]:
    """按 manifest 稳定顺序选择用户指定的非重复查询。"""
    order = manifest.get("query_id_order")  # 读取冻结的顺序。
    mapping = manifest.get("query_intent_files")  # 读取 QueryIntent 文件映射。
    requested = {item.strip() for item in query_ids if item.strip()}  # 规范化命令行选择。
    if not isinstance(order, list) or not isinstance(mapping, dict) or not requested or not requested.issubset(set(order)):  # 拒绝空、未知或不完整输入。
        raise ValueError("Query Agent 预估的 query_id 与 manifest 不一致")  # 不猜测调用范围。
    return [item for item in order if item in requested]  # 保持冻结顺序。


def _write_forecast(payload: dict[str, object], output_path: Path) -> dict[str, object]:
    """向尚不存在的路径写出预估，并返回内容哈希作为确认值。"""
    if output_path.exists():  # 历史预估是调用前审计证据，禁止覆盖。
        raise FileExistsError(f"用量预估输出已存在: {output_path}")  # 要求用户使用新的文件名。
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))  # 哈希不依赖缩进和平台换行。
    confirmed = dict(payload)  # 复制后附加确认值，避免污染原始计算对象。
    confirmed["confirmation_sha256"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()  # 用户后续必须显式确认该值。
    output_path.parent.mkdir(parents=True, exist_ok=True)  # 仅创建用户指定预估文件的父目录。
    output_path.write_text(json.dumps(confirmed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")  # 写入可读 JSON，不写查询正文。
    return confirmed  # 返回与磁盘一致的确认内容。


def _sha256(path: Path) -> str:
    """返回用户指定输入文件的原始字节 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()  # 不解析或输出输入正文。
