"""选择并封存完全离线、可复现的 GoldQuery 开发集子集。"""

import hashlib  # 计算输入、输出和稳定选择键的 SHA-256。
import json  # 将 manifest 写为可读的 UTF-8 JSON。
import os  # 刷新并原子发布 manifest 文件。
from pathlib import Path  # 接收用户显式指定的本地输入和输出路径。
from tempfile import NamedTemporaryFile  # 在 manifest 同目录创建安全临时文件。
from typing import Sequence  # 接收已加载的 GoldQuery 序列。

from evaluation.contracts.gold import GoldQuery  # 读取和输出统一评测金标。
from evaluation.contracts.subset import GoldSubsetManifest  # 返回可复核的子集选择清单。
from evaluation.runners.dataset_import import serialize_gold_queries, write_gold_queries  # 复用规范化 GoldQuery 编码和拒绝覆盖的原子写入边界。
from evaluation.runners.fixture import load_jsonl  # 复用 UTF-8、行号和 Pydantic JSONL 读取边界。


SUBSET_MANIFEST_SCHEMA_VERSION = "gold-subset-manifest-v1"  # 冻结当前 manifest 输出版本。
SUBSET_SELECTION_STRATEGY = "sha256-query-id-v1"  # 冻结只依赖显式输入与 query_id 的稳定排序策略。


def select_gold_subset(
    gold_queries: Sequence[GoldQuery],
    *,
    count: int,
    selection_id: str,
    selection_seed: str,
) -> list[GoldQuery]:
    """从完整 GoldQuery 集合中按稳定 SHA-256 排名选择指定数量的查询。

    参数：
        gold_queries：已从用户明确指定的本地文件读取的完整金标集合。
        count：本次开发集子集大小，不与候选或评分 Top-K 参数复用。
        selection_id：人工冻结的实验用途与版本标识。
        selection_seed：参与 SHA-256 输入的显式种子。
    返回：
        list[GoldQuery]：按稳定选择排名排列且保持原记录内容的子集。
    异常：
        ValueError：输入为空、查询标识重复、数量越界或审计标签非法时抛出。
    """
    normalized_selection_id = _normalize_selection_text(selection_id, "selection_id")  # 规范化但不推断实验名称。
    normalized_selection_seed = _normalize_selection_text(selection_seed, "selection_seed")  # 要求种子由用户显式冻结。
    if not gold_queries:  # 空金标不能建立可比较的开发集。
        raise ValueError("GoldQuery 输入不能为空")  # 在计算哈希前提供明确离线错误。
    if count < 1:  # 子集大小必须形成非空评分分母。
        raise ValueError("count 必须为正整数")  # 不接受零或负数的隐式空实验。
    if count > len(gold_queries):  # 禁止将全量不足误写为成功的固定规模子集。
        raise ValueError(f"count 不能大于输入查询数: {len(gold_queries)}")  # 输出来源总量帮助用户修正命令。
    query_ids = [query.query_id.strip() for query in gold_queries]  # 统一读取稳定关联键并去除边界空白。
    if any(not query_id for query_id in query_ids):  # GoldQuery 契约虽已限制长度，仍保护纯空白边界。
        raise ValueError("GoldQuery 不能包含空白 query_id")  # 避免不可复现哈希输入。
    if any(query.query_id != query_id for query, query_id in zip(gold_queries, query_ids, strict=True)):  # 输出 GoldQuery 与 manifest 必须使用完全相同的稳定关联键。
        raise ValueError("GoldQuery query_id 不能包含前后空白")  # 防止哈希、输出和 manifest 对同一标识采用不同表示。
    if len(set(query_ids)) != len(query_ids):  # 同一源文件的重复查询会扩大评测分母。
        raise ValueError("GoldQuery 输入包含重复 query_id")  # 在选择前拒绝歧义的来源数据。
    ranked_queries = sorted(  # 使用 query_id 次级键消除极低概率哈希碰撞和输入顺序影响。
        gold_queries,
        key=lambda query: (_selection_key(query.query_id, normalized_selection_id, normalized_selection_seed), query.query_id),
    )
    return ranked_queries[:count]  # 只返回用户明确请求的固定规模开发集子集。


def select_gold_subset_to_files(
    input_path: Path,
    *,
    count: int,
    selection_id: str,
    selection_seed: str,
    output_path: Path,
    manifest_path: Path,
) -> GoldSubsetManifest:
    """从本地 GoldQuery JSONL 选择子集，并写入新的 JSONL 与独立 manifest。

    本函数只读取用户提供的本地文件；不读取 `.env`，不调用学术 API、LLM 或本地模型。
    输出与 manifest 都必须尚不存在，防止新一次选择覆盖已用于候选快照的实验输入。
    """
    normalized_selection_id = _normalize_selection_text(selection_id, "selection_id")  # 在 manifest 和稳定哈希前统一用户文本边界。
    normalized_selection_seed = _normalize_selection_text(selection_seed, "selection_seed")  # 防止审计记录与实际参与排序的种子不一致。
    _validate_output_paths(output_path, manifest_path)  # 在读取完整输入前保护两个用户输出路径。
    source_bytes = input_path.read_bytes()  # 读取原始字节以冻结输入内容而不是仅冻结解析后的对象。
    source_gold_sha256 = hashlib.sha256(source_bytes).hexdigest()  # 计算输入 GoldQuery 文件的可复核内容哈希。
    gold_queries = load_jsonl(input_path, GoldQuery)  # 使用正式 UTF-8 与行号边界解析输入金标。
    selected_gold_queries = select_gold_subset(  # 在内存中完成确定性选择，不写入或调用任何外部资源。
        gold_queries,
        count=count,
        selection_id=normalized_selection_id,
        selection_seed=normalized_selection_seed,
    )
    selected_serialized = serialize_gold_queries(selected_gold_queries)  # 复用正式 JSONL 编码以保证输出哈希与文件一致。
    selected_gold_sha256 = hashlib.sha256(selected_serialized.encode("utf-8")).hexdigest()  # 在写入前冻结规范化输出内容哈希。
    manifest = GoldSubsetManifest(  # 构造同时包含算法、输入、输出和完整 ID 列表的可复核清单。
        schema_version=SUBSET_MANIFEST_SCHEMA_VERSION,
        selection_strategy=SUBSET_SELECTION_STRATEGY,
        selection_id=normalized_selection_id,
        selection_seed=normalized_selection_seed,
        source_gold_sha256=source_gold_sha256,
        source_query_count=len(gold_queries),
        selected_query_count=len(selected_gold_queries),
        selected_gold_sha256=selected_gold_sha256,
        selected_query_ids=[query.query_id for query in selected_gold_queries],
    )
    _write_manifest(manifest, manifest_path)  # 先安全发布 manifest；后续 GoldQuery 写入失败时保留清单供用户审计失败输入。
    write_gold_queries(selected_gold_queries, output_path)  # 复用拒绝覆盖和同目录原子发布的 GoldQuery 写入器。
    return manifest  # 返回已与输出 GoldQuery 成对发布的审计清单。


def _selection_key(query_id: str, selection_id: str, selection_seed: str) -> str:
    """计算不依赖输入文件顺序的稳定选择排名键。"""
    payload = "\x00".join((SUBSET_SELECTION_STRATEGY, selection_id, selection_seed, query_id)).encode("utf-8")  # 用不可见分隔符避免字段拼接歧义。
    return hashlib.sha256(payload).hexdigest()  # 返回十六进制哈希以支持稳定字符串排序。


def _normalize_selection_text(value: str, name: str) -> str:
    """校验用户显式提供的子集标识或种子不为空且不含内部边界分隔符。"""
    normalized_value = value.strip()  # 统一移除无语义的首尾空白。
    if not normalized_value:  # 空文本会破坏 manifest 的人工可读审计性。
        raise ValueError(f"{name} 不能为空")  # 返回明确参数名。
    if "\x00" in normalized_value:  # 内部哈希输入固定使用空字符分隔字段。
        raise ValueError(f"{name} 不能包含空字符")  # 防止不同字段组合产生相同字节串。
    return normalized_value  # 返回可安全参与稳定哈希的用户文本。


def _validate_output_paths(output_path: Path, manifest_path: Path) -> None:
    """确认 GoldQuery 与 manifest 输出均为不同且尚不存在的用户路径。"""
    resolved_output_path = output_path.resolve()  # 规范化相对路径和 ``..`` 段以安全比较最终目标。
    resolved_manifest_path = manifest_path.resolve()  # 规范化 manifest 路径以发现同一文件的不同写法。
    if resolved_output_path == resolved_manifest_path:  # 两类内容不能写入同一个文件。
        raise ValueError("output_path 与 manifest_path 必须不同")  # 防止 JSONL 和 JSON 相互覆盖。
    if output_path.exists():  # 在读取输入前保护已封存的开发集子集。
        raise FileExistsError(f"GoldQuery 子集输出已存在: {output_path}")  # 禁止覆盖已进入后续实验的子集。
    if manifest_path.exists():  # 在读取输入前保护已有审计清单。
        raise FileExistsError(f"GoldQuery 子集 manifest 已存在: {manifest_path}")  # 禁止将不同选择规则写到同一清单路径。


def _write_manifest(manifest: GoldSubsetManifest, manifest_path: Path) -> None:
    """通过同目录临时文件将 manifest 原子写入尚不存在的 JSON 文件。"""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)  # 仅创建用户明确指定 manifest 的父目录。
    serialized = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n"  # 使用稳定键序和 UTF-8 文本方便人工复核。
    temporary_path: Path | None = None  # 保存临时文件路径以便异常时安全回收。
    try:  # manifest 写入或发布失败时不得留下不完整目标。
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=manifest_path.parent, prefix=f".{manifest_path.name}.", suffix=".tmp", delete=False) as stream:  # 在同一文件系统创建临时 manifest。
            temporary_path = Path(stream.name)  # 保存临时路径供关闭后发布。
            stream.write(serialized)  # 一次写入已通过 Pydantic 校验的完整 manifest。
            stream.flush()  # 刷新 Python 文本缓冲。
            os.fsync(stream.fileno())  # 请求操作系统完成落盘后再发布。
        if manifest_path.exists():  # 发布前再次检查并发创建的用户文件。
            raise FileExistsError(f"GoldQuery 子集 manifest 已存在: {manifest_path}")  # 保留用户先创建的清单。
        os.replace(temporary_path, manifest_path)  # 在同目录原子发布完整 manifest。
        temporary_path = None  # 标记临时文件已发布，不再需要清理。
    finally:
        if temporary_path is not None and temporary_path.exists():  # 仅回收尚未发布的临时文件。
            temporary_path.unlink()  # 防止错误路径留下临时碎片。
