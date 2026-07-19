"""为评测真实调用生成不访问网络的资源预估。"""

import hashlib  # 固定输入与预估内容的可确认哈希。
import json  # 读写可审阅且不含查询正文的 JSON。
from math import ceil  # 用保守字符比例估算 Token 上限。
from pathlib import Path  # 处理用户明确指定的本地输入和输出。

from backend.app.core.deepseek_pricing import estimate_deepseek_cost_or_zero  # 复用已冻结的本地价格计算。
from backend.app.models.query_intent import QueryIntent  # 校验快照预估所需的生产查询契约。


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
