"""将单查询候选快照按冻结顺序组装为完全离线的共享评测输入。"""

import hashlib  # 计算 QueryIntent manifest 原始字节的 SHA-256。
import json  # 解析 QueryIntent manifest 并稳定写出集合 manifest。
import os  # 通过同目录替换发布完整输出文件。
from datetime import datetime, timezone  # 为集合 manifest 写入明确 UTC 时间。
from pathlib import Path  # 只读取用户明确指定的快照目录和 manifest，并写入新输出。
from tempfile import NamedTemporaryFile  # 先写临时文件以避免半截 JSONL 作为正式输出出现。

from evaluation.contracts.snapshot import CandidateSnapshot  # 保持集合 JSONL 使用既有排序前候选快照契约。
from evaluation.contracts.snapshot_collection import CandidateSnapshotCollectionManifest  # 保存集合选择、哈希和候选数量审计。
from evaluation.runners.snapshot_loader import load_candidate_snapshots, validate_snapshot_integrity  # 复用正式哈希、身份去重和重复快照校验。


QUERY_INTENT_MANIFEST_SCHEMA_VERSION = "query-intent-manifest-v1"  # 仅接受当前已确认的 QueryIntent manifest 契约。
COLLECTION_SELECTION_STRATEGY = "manifest-order-with-explicit-overrides-v1"  # 固定目录扫描、manifest 顺序与显式重试选择的规则。
SOURCE_FAILURE_WARNING_PREFIX = "学术来源降级"  # 历史全部来源失败产物不得进入离线排序集合。


def parse_snapshot_overrides(values: list[str]) -> dict[str, str]:
    """解析 ``query_id=相对快照路径`` 形式的显式重试选择。"""
    overrides: dict[str, str] = {}  # 保存每个存在多个有效重试快照的查询选择。
    for value in values:  # 保持用户参数顺序，但结果只依赖稳定 query_id 键。
        query_id, separator, relative_path = value.partition("=")  # 仅按第一个等号拆分，保留路径中的其他字符。
        normalized_query_id = query_id.strip()  # 不允许查询标识因首尾空白产生不同键。
        normalized_relative_path = relative_path.strip()  # 不允许空路径或无意义首尾空白。
        if not separator or not normalized_query_id or not normalized_relative_path:  # 缺少任一半边时不能猜测用户意图。
            raise ValueError("--snapshot-override 必须为 query_id=相对快照路径")  # 返回可复制修正的参数格式。
        if normalized_query_id in overrides:  # 同一查询不能有两个互相冲突的重试选择。
            raise ValueError(f"--snapshot-override 包含重复 query_id: {normalized_query_id}")  # 防止目录扫描结果不确定。
        relative = Path(normalized_relative_path)  # 用 Path 检查用户给出的映射是否试图逃出快照目录。
        if relative.is_absolute() or ".." in relative.parts:  # 集合 manifest 只保存快照目录内的相对路径。
            raise ValueError("--snapshot-override 必须使用快照目录内的相对路径")  # 阻止读取未声明的外部实验文件。
        overrides[normalized_query_id] = relative.as_posix()  # 固定 manifest 中使用跨平台的正斜杠相对路径。
    return overrides  # 返回已完成基本语法与路径边界校验的选择映射。


def assemble_candidate_snapshot_collection(
    *,
    collection_id: str,
    query_intent_manifest_path: Path,
    snapshot_directory: Path,
    snapshot_overrides: dict[str, str],
    output_path: Path,
    manifest_path: Path,
) -> CandidateSnapshotCollectionManifest:
    """离线组装共享候选集合并写出 JSONL 与审计 manifest。

    该函数不读取 `.env`，不访问网络、学术 API、LLM 或本地模型。每条输入快照都必须通过
    既有 ``snapshot-check`` 同等的完整性校验；来源降级产物会被排除，多个可用重试必须显式选择。
    """
    normalized_collection_id = collection_id.strip()  # 统一人工冻结集合标识的边界空白。
    if not normalized_collection_id:  # 空标识无法在计划、报告和目录中审计本次集合。
        raise ValueError("collection_id 不能为空")  # 在读取任何输入前返回明确错误。
    _validate_new_output_paths(output_path, manifest_path)  # 禁止覆盖已有集合 JSONL 或审计 manifest。
    query_manifest_bytes = query_intent_manifest_path.read_bytes()  # 冻结 QueryIntent manifest 原始内容而非仅解析后的对象。
    query_manifest_sha256 = hashlib.sha256(query_manifest_bytes).hexdigest()  # 计算可复核输入哈希。
    query_manifest = _load_query_intent_manifest(query_manifest_bytes, query_intent_manifest_path)  # 读取已确认字段而不猜测其他版本。
    expected_query_ids = query_manifest["query_id_order"]  # 保留用户此前封存的 20 条稳定顺序。
    source_recall_count = query_manifest["source_recall_count"]  # 固定在线来源召回规模。
    target_paper_count = query_manifest["target_paper_count"]  # 固定在线目标论文数量。
    _validate_overrides_against_manifest(snapshot_overrides, expected_query_ids)  # 在扫描目录前拒绝无效或额外的人工选择。
    candidates_by_query = _load_eligible_snapshot_candidates(snapshot_directory, expected_query_ids)  # 只读加载、验 hash 并排除来源降级产物。
    selected_paths = _select_snapshot_paths(expected_query_ids, candidates_by_query, snapshot_overrides)  # 为每条查询确定唯一可复用快照。
    selected_snapshots: list[CandidateSnapshot] = []  # 按冻结 QueryIntent 顺序保存最终集合输入。
    selected_hashes: dict[str, str] = {}  # 保存每条查询已复核的不可变内容哈希。
    selected_snapshot_ids: dict[str, str] = {}  # 保存每条查询对应的单查询快照标识。
    candidate_counts: dict[str, int] = {}  # 保存真实排序输入数量，允许少于目标数量。
    selected_relative_paths: dict[str, str] = {}  # 保存相对快照目录的可移植来源路径。
    for query_id in expected_query_ids:  # 严格按已封存顺序而非文件名或创建时间组装输出。
        snapshot_path, snapshot = selected_paths[query_id]  # 读取已在目录扫描中完成正式加载的唯一候选。
        _validate_snapshot_matches_query_manifest(snapshot, query_id, source_recall_count, target_paper_count)  # 防止快照元数据与 QueryIntent 冻结参数漂移。
        selected_snapshots.append(snapshot)  # 将原始排序前快照对象加入共享 JSONL。
        selected_hashes[query_id] = validate_snapshot_integrity(snapshot)  # 再次计算并保存实际 SHA-256，防止选择逻辑绕过完整性边界。
        selected_snapshot_ids[query_id] = snapshot.snapshot_id  # 冻结后续任务和结果可引用的快照 ID。
        candidate_counts[query_id] = snapshot.ranking_candidate_count  # 保留真实候选不足事实供报告使用。
        selected_relative_paths[query_id] = snapshot_path.relative_to(snapshot_directory.resolve()).as_posix()  # 不在 manifest 中写入机器绝对路径。
    serialized_snapshots = "".join(snapshot.model_dump_json() + "\n" for snapshot in selected_snapshots)  # 按稳定顺序输出一行一份的正式 CandidateSnapshot JSONL。
    collection_manifest = CandidateSnapshotCollectionManifest(  # 建立可独立审计集合来源、哈希、顺序和候选规模的 manifest。
        selection_strategy=COLLECTION_SELECTION_STRATEGY,
        collection_id=normalized_collection_id,
        query_intent_manifest_sha256=query_manifest_sha256,
        source_recall_count=source_recall_count,
        target_paper_count=target_paper_count,
        query_id_order=expected_query_ids,
        snapshot_directory=str(snapshot_directory),
        selected_snapshot_paths=selected_relative_paths,
        selected_snapshot_ids=selected_snapshot_ids,
        selected_snapshot_hashes=selected_hashes,
        ranking_candidate_counts=candidate_counts,
        created_at=datetime.now(timezone.utc),
    )
    _write_new_text_file(output_path, serialized_snapshots, "候选快照集合输出")  # 先发布已完整验证的 JSONL，绝不修改任何输入快照。
    _write_new_text_file(manifest_path, json.dumps(collection_manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n", "候选快照集合 manifest")  # 再发布与 JSONL 同次生成的审计记录。
    return collection_manifest  # 返回调用方可显示数量和集合标识的 manifest。


def _load_query_intent_manifest(raw_bytes: bytes, source_path: Path) -> dict[str, object]:
    """读取并校验组装所需的最小 QueryIntent manifest 字段。"""
    try:  # 将 JSON 语法与类型错误统一为不含查询正文的本地输入错误。
        payload = json.loads(raw_bytes.decode("utf-8"))  # 只接受仓库统一要求的 UTF-8 文本。
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # 不兼容编码或损坏 manifest 不能被猜测修复。
        raise ValueError(f"QueryIntent manifest 无效: {source_path}") from exc  # 返回用户显式输入路径而不回显内容。
    if not isinstance(payload, dict) or payload.get("schema_version") != QUERY_INTENT_MANIFEST_SCHEMA_VERSION:  # 当前组装器只接受已确认 manifest 版本。
        raise ValueError("QueryIntent manifest 必须为 query-intent-manifest-v1")  # 阻止未迁移格式静默进入评测。
    query_id_order = payload.get("query_id_order")  # 读取冻结的稳定查询顺序。
    source_recall_count = payload.get("source_recall_count")  # 读取线上候选规模边界。
    target_paper_count = payload.get("target_paper_count")  # 读取最终目标数量。
    query_intent_files = payload.get("query_intent_files")  # 读取映射以验证 manifest 自身完整性。
    if not isinstance(query_id_order, list) or not query_id_order or any(not isinstance(query_id, str) or not query_id.strip() for query_id in query_id_order):  # 空或非文本 ID 无法作为集合稳定键。
        raise ValueError("QueryIntent manifest 的 query_id_order 无效")  # 返回明确字段边界。
    if len(set(query_id_order)) != len(query_id_order):  # 同一查询只允许对应一份候选快照。
        raise ValueError("QueryIntent manifest 的 query_id_order 包含重复 query_id")  # 防止集合输出重复评测分母。
    if not isinstance(source_recall_count, int) or isinstance(source_recall_count, bool) or not 1 <= source_recall_count <= 100:  # 与 CandidateSnapshot 契约保持相同范围。
        raise ValueError("QueryIntent manifest 的 source_recall_count 无效")  # 阻止在线规模不明确。
    if not isinstance(target_paper_count, int) or isinstance(target_paper_count, bool) or not 1 <= target_paper_count <= 100:  # 与 CandidateSnapshot 契约保持相同范围。
        raise ValueError("QueryIntent manifest 的 target_paper_count 无效")  # 阻止最终数量不明确。
    if not isinstance(query_intent_files, dict) or set(query_intent_files) != set(query_id_order):  # 组装前确保每条冻结查询确有对应意图输入记录。
        raise ValueError("QueryIntent manifest 的 query_intent_files 必须完整覆盖 query_id_order")  # 防止顺序与映射漂移。
    return {"query_id_order": list(query_id_order), "source_recall_count": source_recall_count, "target_paper_count": target_paper_count}  # 返回仅供离线组装所需的已验证字段。


def _validate_overrides_against_manifest(overrides: dict[str, str], expected_query_ids: list[str]) -> None:
    """确认显式重试选择只作用于当前集合中已冻结的查询。"""
    unknown_query_ids = sorted(set(overrides) - set(expected_query_ids))  # 收集用户可能拼错或跨实验复制的查询标识。
    if unknown_query_ids:  # 不能默默忽略无效选择，否则用户以为重试选择已生效。
        raise ValueError(f"--snapshot-override 包含 manifest 之外的 query_id: {unknown_query_ids[0]}")  # 只显示稳定标识，不输出文件内容。


def _load_eligible_snapshot_candidates(snapshot_directory: Path, expected_query_ids: list[str]) -> dict[str, list[tuple[Path, CandidateSnapshot]]]:
    """加载目录内属于当前集合的无来源降级快照，并保留多个成功重试供显式选择。"""
    if not snapshot_directory.is_dir():  # 目录不存在时不能扫描或创建替代输入。
        raise ValueError(f"候选快照目录不存在: {snapshot_directory}")  # 要求用户显式提供真实已完成目录。
    expected_set = set(expected_query_ids)  # 建立当前集合允许的查询范围。
    candidates: dict[str, list[tuple[Path, CandidateSnapshot]]] = {query_id: [] for query_id in expected_query_ids}  # 即使尚未找到快照也保留键以便统一报错。
    for snapshot_path in sorted(snapshot_directory.glob("*.snapshot.jsonl")):  # 文件名排序只影响错误稳定性，不影响最终 QueryIntent 顺序。
        snapshots = load_candidate_snapshots(snapshot_path)  # 每个独立文件必须通过正式哈希、契约和身份去重校验。
        if len(snapshots) != 1:  # snapshot-export 每次只能写一份单查询快照。
            raise ValueError(f"单查询候选快照文件必须恰好包含一份记录: {snapshot_path}")  # 拒绝被手工拼接的目录输入。
        snapshot = snapshots[0]  # 读取已完成结构校验的单个快照。
        if snapshot.query_id not in expected_set:  # 当前目录不能混入其他数据集或实验子集的候选。
            raise ValueError(f"候选快照包含 QueryIntent manifest 之外的 query_id: {snapshot.query_id}")  # 防止误用相邻实验结果。
        if any(warning.startswith(SOURCE_FAILURE_WARNING_PREFIX) for warning in snapshot.warnings):  # 全部来源失败的旧产物不得被快照哈希掩盖。
            continue  # 忽略失败重试并继续寻找该查询后续成功快照。
        candidates[snapshot.query_id].append((snapshot_path.resolve(), snapshot))  # 保留所有无来源降级的有效重试供下一步唯一选择。
    return candidates  # 返回按 query_id 分组的可复用成功快照。


def _select_snapshot_paths(expected_query_ids: list[str], candidates_by_query: dict[str, list[tuple[Path, CandidateSnapshot]]], overrides: dict[str, str]) -> dict[str, tuple[Path, CandidateSnapshot]]:
    """为每条查询选择唯一候选；存在多个成功重试时要求用户显式指定。"""
    selected: dict[str, tuple[Path, CandidateSnapshot]] = {}  # 保存最终一对一的查询到快照映射。
    for query_id in expected_query_ids:  # 保持输入 manifest 的稳定顺序和完整覆盖要求。
        candidates = candidates_by_query[query_id]  # 读取已通过完整性校验且无来源降级的候选集合。
        override_path = overrides.get(query_id)  # 多个成功重试时读取用户显式选择。
        if override_path is not None:  # 用户已声明必须使用的目录内相对文件。
            selected_candidate = next((candidate for candidate in candidates if candidate[0].name == Path(override_path).name), None)  # 当前目录只扫描单层 JSONL，因此文件名即可唯一匹配显式选择。
            if selected_candidate is None:  # 显式路径不存在、属于来源降级产物或 query_id 不匹配都不能悄悄回退。
                raise ValueError(f"--snapshot-override 未找到可用快照: {query_id}={override_path}")  # 强制用户修正选择而不是混入其他重试。
            selected[query_id] = selected_candidate  # 固定用户明确确认的重试结果。
            continue  # 已选择后不再根据候选数量自动推断。
        if not candidates:  # 没有成功快照说明当前集合尚未完成。
            raise ValueError(f"查询缺少无来源降级的有效候选快照: {query_id}")  # 阻止不完整集合进入离线消融。
        if len(candidates) > 1:  # 多个成功重试会使集合内容和哈希不再唯一。
            raise ValueError(f"查询存在多个有效候选快照，请使用 --snapshot-override 选择: {query_id}")  # 要求用户显式冻结本次评测输入。
        selected[query_id] = candidates[0]  # 唯一成功快照可安全自动选择。
    return selected  # 返回完整且无歧义的查询映射。


def _validate_snapshot_matches_query_manifest(snapshot: CandidateSnapshot, query_id: str, source_recall_count: int, target_paper_count: int) -> None:
    """确认快照及其冻结 QueryIntent 与集合 manifest 共享同一在线候选边界。"""
    if snapshot.query_id != query_id:  # 选择映射不能将相邻文件错误关联到当前查询。
        raise ValueError(f"候选快照 query_id 不匹配: {snapshot.snapshot_id}")  # 返回快照标识而不回显查询正文。
    if snapshot.source_recall_count != source_recall_count:  # 召回规模改变必须重新生成在线候选，而非与旧快照混用。
        raise ValueError(f"候选快照 source_recall_count 不匹配: {snapshot.snapshot_id}")  # 拒绝不可横向比较的输入。
    if snapshot.target_paper_count != target_paper_count:  # 在线目标数量同样属于冻结候选生成边界。
        raise ValueError(f"候选快照 target_paper_count 不匹配: {snapshot.snapshot_id}")  # 防止不同工作流目标混入集合。
    query_intent_source_count = snapshot.query_intent.get("source_recall_count")  # 从快照内冻结 QueryIntent 再次读取在线召回边界。
    query_intent_target_count = snapshot.query_intent.get("target_paper_count")  # 从快照内冻结 QueryIntent 再次读取最终目标数量。
    if query_intent_source_count != source_recall_count or query_intent_target_count != target_paper_count:  # 快照外层与内部 QueryIntent 必须表示同一次候选生成配置。
        raise ValueError(f"候选快照 QueryIntent 参数不匹配: {snapshot.snapshot_id}")  # 阻止手工篡改或旧契约漂移。


def _validate_new_output_paths(output_path: Path, manifest_path: Path) -> None:
    """确认两个输出路径不同且尚不存在，禁止覆盖任何已封存集合。"""
    if output_path.resolve() == manifest_path.resolve():  # JSONL 与 JSON manifest 不能共用同一目标。
        raise ValueError("候选快照集合 output 与 manifest 路径必须不同")  # 防止两种格式互相覆盖。
    if output_path.exists():  # 已存在输出可能已经被审阅或用于后续计划。
        raise FileExistsError(f"候选快照集合输出已存在: {output_path}")  # 要求用户使用新的版本路径。
    if manifest_path.exists():  # 已存在 manifest 同样不得被新选择覆盖。
        raise FileExistsError(f"候选快照集合 manifest 已存在: {manifest_path}")  # 保留既有审计记录。


def _write_new_text_file(path: Path, text: str, label: str) -> None:
    """通过同目录临时文件写入一个此前不存在的 UTF-8 文本输出。"""
    path.parent.mkdir(parents=True, exist_ok=True)  # 仅创建用户明确指定输出的父目录。
    temporary_path: Path | None = None  # 记录未发布临时文件以便异常时清理。
    try:  # 写入或发布失败时不留下半截正式输出。
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:  # 在同一文件系统创建可原子替换的临时文件。
            temporary_path = Path(stream.name)  # 保存临时路径供关闭后发布。
            stream.write(text)  # 一次写入调用方已在内存中验证的完整文本。
            stream.flush()  # 刷新 Python 文本缓冲区。
            os.fsync(stream.fileno())  # 请求操作系统在发布前完成数据落盘。
        if path.exists():  # 发布前再次检查并发创建，禁止覆盖用户文件。
            raise FileExistsError(f"{label} 已存在: {path}")  # 保留先创建的人工审阅产物。
        os.replace(temporary_path, path)  # 同目录原子发布完整输出。
        temporary_path = None  # 标记已发布，finally 不应清理正式文件。
    finally:
        if temporary_path is not None and temporary_path.exists():  # 仅删除发布失败遗留的临时文件。
            temporary_path.unlink()  # 防止本地评测目录积累不完整碎片。
