"""提供只接收既有 QueryIntent 的受控评测 Query Agent 规划入口。"""

import hashlib  # 冻结输入、输出与单文件内容哈希。
import json  # 读写 UTF-8 manifest 和 QueryIntent JSON。
import os  # 原子发布单个新文件。
import re  # 清理 Windows 不允许出现在文件名中的字符。
from collections.abc import Awaitable, Callable  # 声明可注入的异步规划器协议。
from pathlib import Path  # 处理用户显式指定的本地路径。
from tempfile import NamedTemporaryFile  # 避免写出半截 QueryIntent 文件。
from typing import Protocol  # 限制运行器不依赖具体生产服务。

from backend.app.models.natural_search import NaturalSearchRequest, QueryPlanningResult  # 复用生产输入输出契约但不导入配置。
from backend.app.models.query_intent import QueryIntent  # 复用生产 QueryIntent 严格校验。


class EvaluationQueryPlanner(Protocol):
    """声明评测规划器所需的最小异步协议。"""

    def plan(self, request: NaturalSearchRequest) -> Awaitable[QueryPlanningResult]:
        """只根据自然语言查询和显式条件返回结构化 QueryIntent。"""
        ...  # 生产服务与零网络替身均可满足该协议。


def validate_query_agent_request(*, input_manifest_path: Path, query_ids: list[str], output_dir: Path, manifest_path: Path) -> None:
    """在导入生产 Query Agent 或读取 .env 前校验授权后的静态路径和查询选择。"""
    if not input_manifest_path.is_file():  # 输入 manifest 必须是用户明确选择的现有文件。
        raise FileNotFoundError(f"Query Agent 输入 manifest 不存在: {input_manifest_path}")  # 不猜测默认评测集。
    if output_dir.exists():  # 规划输出不可覆盖历史人工审阅或调用记录。
        raise FileExistsError(f"Query Agent 输出目录已存在: {output_dir}")  # 要求使用新的实验版本目录。
    if manifest_path.exists():  # 审计 manifest 同样不可覆盖。
        raise FileExistsError(f"Query Agent 输出 manifest 已存在: {manifest_path}")  # 保留历史成本与输入证据。
    if output_dir.resolve() == manifest_path.resolve():  # 目录与文件不能共用同一路径。
        raise ValueError("Query Agent 输出目录与 manifest 路径必须不同")  # 防止文件系统类型冲突。
    normalized_ids = [query_id.strip() for query_id in query_ids]  # 清理命令行中无语义空白。
    if not normalized_ids or any(not query_id for query_id in normalized_ids):  # 空查询选择会导致无边界 LLM 消耗。
        raise ValueError("至少需要一个非空 --query-id")  # 强制用户显式决定调用范围。
    if len(set(normalized_ids)) != len(normalized_ids):  # 重复选择会重复消耗 LLM 并覆盖文件名。
        raise ValueError("--query-id 不得重复")  # 保持调用与输出一一对应。


async def plan_query_intents_to_files(*, planner: EvaluationQueryPlanner, input_manifest_path: Path, query_ids: list[str], output_dir: Path, manifest_path: Path) -> dict[str, object]:
    """以用户显式选择的既有 QueryIntent 为唯一语义输入，生成新的评测 QueryIntent 文件。

    参数：规划器、输入 manifest、待规划 query_id、全新输出目录和全新审计 manifest。
    返回：不含查询正文的调用与输出审计摘要。
    异常：输入、Query Agent 输出或写入边界不合法时抛出，且不覆盖既有文件。
    """
    validate_query_agent_request(input_manifest_path=input_manifest_path, query_ids=query_ids, output_dir=output_dir, manifest_path=manifest_path)  # 在任何 LLM 调用前完成静态保护。
    source_manifest = _load_input_manifest(input_manifest_path)  # 只读取 QueryIntent 路径映射，不接受 Gold 或候选输入。
    selected_ids = _select_query_ids(source_manifest, query_ids)  # 保持 source manifest 的稳定顺序。
    prepared_outputs: list[tuple[str, str, QueryPlanningResult, str]] = []  # 在内存中累积全部 LLM 成功结果后再写文件。
    for ordinal, query_id in enumerate(selected_ids, start=1):  # 按冻结顺序逐条执行，避免隐藏批量成本。
        source_path = Path(source_manifest["query_intent_files"][query_id])  # 只从 manifest 显式映射读取原始 QueryIntent。
        source_intent = QueryIntent.model_validate_json(source_path.read_text(encoding="utf-8"))  # 不读取 Gold、候选、报告或 .env。
        request = NaturalSearchRequest(  # 仅传递原问题和原输入已显式给出的条件。
            query=source_intent.original_query,
            search_mode=source_intent.search_mode,
            enable_semantic_ranking=False,
            enable_cross_encoder_ranking=False,
            year_range=source_intent.year_range,
            must_include=source_intent.must_include,
            should_include=source_intent.should_include,
            exclude=source_intent.exclude,
            domains=source_intent.domains,
            requires_web_evidence=False,
            target_paper_count=source_intent.target_paper_count,
        )
        planned_result = await planner.plan(request)  # 唯一可能触发 LLM 的边界，由调用方显式授权后注入。
        planned_intent = planned_result.query_intent.model_copy(update={  # 强制恢复评测候选快照的固定参数边界。
            "target_paper_count": source_intent.target_paper_count,
            "source_recall_count": source_intent.source_recall_count,
            "retrieval_round": 1,
            "search_mode": source_intent.search_mode,
            "enable_semantic_ranking": False,
            "enable_cross_encoder_ranking": False,
            "requires_web_evidence": False,
            "subqueries": [],
        })
        serialized = planned_intent.model_dump_json(indent=2) + "\n"  # 使用可审阅 UTF-8 JSON 编码。
        output_name = _query_intent_filename(ordinal, query_id)  # Windows 安全文件名不直接使用冒号。
        prepared_outputs.append((query_id, output_name, planned_result, serialized))  # 延迟写入直到全部选择查询均规划成功。
    _write_outputs(output_dir, manifest_path, input_manifest_path, source_manifest, prepared_outputs)  # 发布新的 QueryIntent 文件和审计 manifest。
    return json.loads(manifest_path.read_text(encoding="utf-8"))  # 返回已落盘且不含查询正文的审计摘要。


def _load_input_manifest(path: Path) -> dict[str, object]:
    """读取最小 QueryIntent manifest 契约，拒绝 Gold、候选或其他输入格式。"""
    try:  # 将 JSON 和字段错误转换为稳定本地输入错误。
        payload = json.loads(path.read_text(encoding="utf-8"))  # 只读取用户显式路径。
    except Exception as error:
        raise ValueError(f"Query Agent 输入 manifest 无效: {path}") from error  # 不回显输入正文。
    if not isinstance(payload, dict) or payload.get("schema_version") != "query-intent-manifest-v1":  # 仅接受当前已确认的 QueryIntent 映射格式。
        raise ValueError("Query Agent 输入必须为 query-intent-manifest-v1")  # 阻止 Gold 或候选文件被误作输入。
    query_id_order = payload.get("query_id_order")  # 读取稳定查询顺序。
    file_mapping = payload.get("query_intent_files")  # 读取明确文件映射。
    if not isinstance(query_id_order, list) or not query_id_order or not all(isinstance(query_id, str) and query_id.strip() for query_id in query_id_order):  # 查询顺序必须可审计。
        raise ValueError("Query Agent 输入 manifest 的 query_id_order 无效")  # 拒绝空或不规范标识。
    if len(set(query_id_order)) != len(query_id_order) or not isinstance(file_mapping, dict) or set(file_mapping) != set(query_id_order):  # 映射必须完整且无重复。
        raise ValueError("Query Agent 输入 manifest 的查询映射无效")  # 阻止漏项或额外输入。
    if any(not isinstance(path_value, str) or not path_value.strip() for path_value in file_mapping.values()):  # 每个映射必须是显式本地路径。
        raise ValueError("Query Agent 输入 manifest 包含无效 QueryIntent 路径")  # 防止隐式默认文件。
    return payload  # 返回已完成最小安全校验的 manifest。


def _select_query_ids(source_manifest: dict[str, object], requested_ids: list[str]) -> list[str]:
    """按输入 manifest 顺序选择用户明确请求的查询，并拒绝未知标识。"""
    requested = {query_id.strip() for query_id in requested_ids}  # 建立去空白选择集合。
    query_id_order = source_manifest["query_id_order"]  # 读取已在加载时验证的稳定顺序。
    unknown = requested - set(query_id_order)  # 找出不属于该 manifest 的查询。
    if unknown:
        raise ValueError(f"Query Agent 输入 manifest 不包含 query_id: {sorted(unknown)[0]}")  # 不静默跳过用户请求。
    return [query_id for query_id in query_id_order if query_id in requested]  # 保持冻结顺序而非命令行顺序。


def _query_intent_filename(ordinal: int, query_id: str) -> str:
    """生成不含 Windows 保留字符的稳定 QueryIntent 文件名。"""
    source_id = query_id.rsplit(":", maxsplit=1)[-1]  # 仅保留 PaSa 原始查询标识部分。
    safe_source_id = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", source_id).strip(". ")  # 同时处理冒号、路径分隔符和控制字符。
    safe_source_id = safe_source_id or "query"  # 不允许清理后生成空文件基名。
    return f"{ordinal:03d}_{safe_source_id}.query-intent.json"  # 使用序号前缀避免 Windows 保留名冲突并保持稳定排序。


def _write_outputs(output_dir: Path, manifest_path: Path, input_manifest_path: Path, source_manifest: dict[str, object], prepared_outputs: list[tuple[str, str, QueryPlanningResult, str]]) -> None:
    """写入新的 QueryIntent 文件与审计 manifest；失败仅清理当前新目录。"""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)  # 仅创建用户明确指定审计文件的父目录。
    output_dir.mkdir(parents=True, exist_ok=False)  # 创建必须此前不存在的用户指定目录。
    try:  # 单条文件失败时不发布残缺的 manifest。
        file_mapping: dict[str, str] = {}  # 保存 query_id 到相对文件路径的稳定映射。
        executions: list[dict[str, object]] = []  # 保存不含问题正文和模型输出正文的用量审计。
        for query_id, output_name, planned_result, serialized in prepared_outputs:  # 按已规划成功顺序写入全部 JSON。
            output_path = output_dir / output_name  # 所有输出限定在本次新目录。
            _write_new_text_file(output_path, serialized)  # 使用同目录临时文件发布完整 QueryIntent。
            file_mapping[query_id] = output_path.as_posix()  # 保存可跨 Windows/CI 读取的相对样式路径。
            executions.append({"query_id": query_id, "model_name": planned_result.model_name, "prompt_tokens": planned_result.prompt_tokens, "completion_tokens": planned_result.completion_tokens, "estimated_cost_cny": planned_result.estimated_cost_cny, "peak_pricing_applied": planned_result.peak_pricing_applied, "duration_ms": planned_result.duration_ms, "query_intent_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest()})  # 冻结每条输出与实际 LLM 用量。
        manifest = {"schema_version": "evaluation-query-agent-manifest-v1", "generation_strategy": "explicit-query-agent-v1", "input_query_intent_manifest_sha256": hashlib.sha256(input_manifest_path.read_bytes()).hexdigest(), "input_generation_strategy": source_manifest.get("generation_strategy"), "query_id_order": [record["query_id"] for record in executions], "query_intent_files": file_mapping, "executions": executions, "academic_api_calls": 0, "deepseek_calls": len(executions), "local_model_calls": 0}  # 明确规划阶段只消耗用户授权的 Query Agent。
        _write_new_text_file(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")  # 单独发布不可覆盖的审计文件。
    except Exception:
        for child in output_dir.iterdir():  # 只清理本函数新建目录中的本次失败产物。
            child.unlink()  # 不递归触碰输出目录外的任何用户文件。
        output_dir.rmdir()  # 失败不保留误导性半成品目录。
        raise


def _write_new_text_file(path: Path, text: str) -> None:
    """以 UTF-8 临时文件原子发布此前不存在的文本输出。"""
    temporary_path: Path | None = None  # 保存异常时需清理的临时文件。
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:  # 在同一目录创建临时文件。
            temporary_path = Path(stream.name)  # 关闭后才能替换到正式路径。
            stream.write(text)  # 写入完整内存序列化内容。
            stream.flush()  # 刷新 Python 缓冲。
            os.fsync(stream.fileno())  # 请求操作系统落盘。
        if path.exists():  # 并发创建同名文件时拒绝覆盖。
            raise FileExistsError(f"Query Agent 输出已存在: {path}")  # 保留先出现的人工产物。
        os.replace(temporary_path, path)  # 原子发布完整文件。
        temporary_path = None  # 标记临时路径已发布。
    finally:
        if temporary_path is not None and temporary_path.exists():  # 仅清理未发布的临时文件。
            temporary_path.unlink()  # 防止输出目录遗留碎片。
