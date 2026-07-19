"""执行已计划的离线排序子集，并以原子 JSONL 与 manifest 归档结果。"""

import hashlib  # 冻结计划、矩阵、快照和结果的原始字节哈希。
import json  # 写入可审阅的 UTF-8 manifest。
import os  # 使用同目录原子替换发布完整输出。
from datetime import datetime, timezone  # 生成带时区的归档时间。
from pathlib import Path  # 处理用户显式提供的本地路径。
from tempfile import NamedTemporaryFile  # 避免写入半截正式结果文件。

from evaluation.adapters.base import OfflineRankingScorer  # 接收调用方显式提供的本地评分器。
from evaluation.contracts.ablation import AblationMatrix, AblationPlan, OfflineAblationResult  # 复用现有计划和运行结果契约。
from evaluation.contracts.offline_run import OfflineRankingRunManifest  # 输出独立可审计的执行 manifest。
from evaluation.runners.offline_ranking import load_ablation_matrix, run_ablation_matrix  # 复用同快照深拷贝与稳定排序逻辑。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 只读加载并复核候选集合。


def execute_ablation_to_files(*, run_id: str, snapshots_path: Path, matrix_path: Path, plan_path: Path, experiment_ids: list[str], output_path: Path, manifest_path: Path, semantic_scorer: OfflineRankingScorer | None = None) -> OfflineRankingRunManifest:
    """执行计划内的 A/B 离线排序子集并归档；不访问网络、LLM 或生产搜索。

    异常：
        ValueError：计划、实验选择或模型阶段不匹配时抛出。
        FileExistsError：结果或 manifest 已存在时抛出，绝不覆盖旧归档。
    """
    normalized_run_id = run_id.strip()  # 去除无语义空白以形成稳定归档标识。
    if not normalized_run_id:  # 空标识无法关联用户一次显式本地执行。
        raise ValueError("run_id 不能为空")  # 在读取模型或写输出前失败。
    _validate_new_output_paths(output_path, manifest_path)  # 先确认归档目标不会覆盖既有结果。
    snapshots = load_candidate_snapshots(snapshots_path)  # 只读校验全部候选快照与 SHA-256。
    matrix = load_ablation_matrix(matrix_path)  # 只读加载并校验 A/B/C/D 数量边界。
    plan = _load_ablation_plan(plan_path)  # 只读加载用户已审核的零 API、零 DeepSeek 计划。
    selected_matrix = _select_supported_experiments(matrix, experiment_ids)  # 仅选择用户明确指定且当前可执行的实验。
    _validate_plan_inputs(plan, snapshots, matrix, selected_matrix)  # 确认执行仍复用计划冻结的同一输入。
    requires_bge = any(experiment.ranking_config.semantic_ranking_enabled for experiment in selected_matrix.experiments)  # 判断是否实际需要本地 BGE-M3。
    if requires_bge and semantic_scorer is None:  # 不允许运行器自行实例化或下载模型。
        raise ValueError("所选实验启用 BGE-M3，必须显式提供 semantic_scorer")  # 强制调用方通过受控适配层授权。
    results = run_ablation_matrix(snapshots, selected_matrix, semantic_scorer=semantic_scorer)  # 复用同一集合快照，不调用学术 API、LLM 或 DeepSeek。
    serialized_results = "".join(result.model_dump_json() + "\n" for result in results)  # 按快照、实验稳定顺序写入一行一个结果。
    result_sha256 = hashlib.sha256(serialized_results.encode("utf-8")).hexdigest()  # 在发布前冻结完整结果字节内容。
    local_model_stages = sorted({trace.stage for result in results for trace in result.stage_traces if trace.enabled and trace.stage in {"bge_m3", "cross_encoder"}})  # 仅记录已实际执行的本地阶段。
    manifest = OfflineRankingRunManifest(  # 组装不包含模型路径和查询正文的结果审计记录。
        run_id=normalized_run_id,
        matrix_id=matrix.matrix_id,
        matrix_sha256=_sha256_file(matrix_path),
        ablation_plan_sha256=_sha256_file(plan_path),
        snapshots_sha256=_sha256_file(snapshots_path),
        result_sha256=result_sha256,
        selected_experiment_ids=[experiment.experiment_id for experiment in selected_matrix.experiments],
        snapshot_ids=[snapshot.snapshot_id for snapshot in snapshots],
        snapshot_hashes={snapshot.snapshot_id: snapshot.snapshot_hash or "" for snapshot in snapshots},
        task_count=len(results),
        local_model_stages=local_model_stages,
        created_at=datetime.now(timezone.utc),
    )
    _write_new_text_file(output_path, serialized_results, "离线排序结果")  # 先原子发布完整 JSONL。
    _write_new_text_file(manifest_path, json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n", "离线排序 manifest")  # 再发布配套审计记录。
    return manifest  # 返回安全摘要所需的任务数量与阶段信息。


def _select_supported_experiments(matrix: AblationMatrix, requested_ids: list[str]) -> AblationMatrix:
    """按矩阵既定顺序选择实验，并拒绝未实现的 Cross Encoder 阶段。"""
    normalized_ids = [experiment_id.strip() for experiment_id in requested_ids]  # 统一命令行重复参数中的无语义空白。
    if not normalized_ids or any(not experiment_id for experiment_id in normalized_ids):  # 空选择会误以为执行了完整矩阵。
        raise ValueError("至少需要一个非空 --experiment")  # 要求用户明确控制本次本地成本。
    if len(set(normalized_ids)) != len(normalized_ids):  # 重复实验会写出重复结果。
        raise ValueError("--experiment 不得重复")  # 保持归档一对一。
    available = {experiment.experiment_id: experiment for experiment in matrix.experiments}  # 建立矩阵内稳定实验索引。
    unknown = sorted(set(normalized_ids) - set(available))  # 收集拼写错误或跨矩阵 ID。
    if unknown:  # 不允许静默跳过用户请求的配置。
        raise ValueError(f"消融矩阵不包含 experiment_id: {unknown[0]}")  # 返回单个稳定错误。
    selected = [experiment for experiment in matrix.experiments if experiment.experiment_id in normalized_ids]  # 保持矩阵定义顺序而非命令行顺序。
    unsupported = next((experiment for experiment in selected if experiment.ranking_config.cross_encoder_ranking_enabled), None)  # 当前闭环尚未实现 Cross Encoder 适配器。
    if unsupported is not None:  # C 和 D 不能伪装为已执行。
        raise ValueError(f"experiment {unsupported.experiment_id} 需要 Cross Encoder，当前尚未实现离线适配器")  # 指向下一独立实施阶段。
    return AblationMatrix(matrix_id=matrix.matrix_id, experiments=selected)  # 保留原矩阵全部共享参数校验。


def _validate_plan_inputs(plan: AblationPlan, snapshots: list, matrix: AblationMatrix, selected_matrix: AblationMatrix) -> None:
    """确认本次执行仍是既有计划的子集，且没有替换候选或矩阵。"""
    if plan.matrix_id != matrix.matrix_id:  # 计划与当前矩阵必须属于同一冻结比较。
        raise ValueError("消融计划 matrix_id 与当前矩阵不一致")  # 阻止跨计划复用。
    snapshot_ids = [snapshot.snapshot_id for snapshot in snapshots]  # 读取当前集合文件顺序。
    if plan.snapshot_ids != snapshot_ids:  # 顺序改变会影响 JSONL 归档可复现性。
        raise ValueError("消融计划 snapshot_ids 与当前候选集合不一致")  # 要求重新生成计划。
    actual_hashes = {snapshot.snapshot_id: snapshot.snapshot_hash for snapshot in snapshots}  # 收集已加载快照的封存摘要。
    if plan.snapshot_hashes != actual_hashes:  # 内容或选择变化都必须重新计划。
        raise ValueError("消融计划 snapshot_hashes 与当前候选集合不一致")  # 禁止替换在线候选。
    selected_ids = [experiment.experiment_id for experiment in selected_matrix.experiments]  # 读取当前请求的可执行子集。
    if not set(selected_ids).issubset(plan.experiment_ids):  # 计划外配置没有审核和任务规模记录。
        raise ValueError("所选 experiment 不在消融计划中")  # 防止借执行入口绕过计划。


def _load_ablation_plan(path: Path) -> AblationPlan:
    """以 UTF-8 读取已有计划，不创建或修改计划文件。"""
    try:  # 统一 JSON 与契约错误为本地输入错误。
        return AblationPlan.model_validate_json(path.read_text(encoding="utf-8"))  # 只读取用户显式提供的计划。
    except Exception as error:  # 路径、编码、JSON 或 Pydantic 错误都不能静默回退。
        raise ValueError(f"消融计划无效: {path}") from error  # 不回显计划正文。


def _sha256_file(path: Path) -> str:
    """计算用户显式本地输入或新结果文件的原始字节 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()  # 保持与不同 JSON 空白格式无关的原始归档证据。


def _validate_new_output_paths(output_path: Path, manifest_path: Path) -> None:
    """确认两个归档输出路径不同且都尚不存在。"""
    if output_path.resolve() == manifest_path.resolve():  # JSONL 与 JSON 不能共用同一文件。
        raise ValueError("离线排序 output 与 manifest 路径必须不同")  # 防止格式互相覆盖。
    if output_path.exists():  # 已存在结果可能已被评分或人工审阅。
        raise FileExistsError(f"离线排序结果已存在: {output_path}")  # 保留历史归档。
    if manifest_path.exists():  # 已存在 manifest 同样不得覆盖。
        raise FileExistsError(f"离线排序 manifest 已存在: {manifest_path}")  # 保留审计证据。


def _write_new_text_file(path: Path, text: str, label: str) -> None:
    """以同目录临时文件原子发布此前不存在的 UTF-8 输出。"""
    path.parent.mkdir(parents=True, exist_ok=True)  # 仅创建用户明确指定输出的父目录。
    temporary_path: Path | None = None  # 保存失败时需要清理的临时文件。
    try:  # 防止异常留下半截正式结果。
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:  # 同一文件系统支持原子替换。
            temporary_path = Path(stream.name)  # 关闭后才能安全替换正式路径。
            stream.write(text)  # 一次性写入已在内存完整序列化的内容。
            stream.flush()  # 刷新 Python 缓冲。
            os.fsync(stream.fileno())  # 请求发布前落盘。
        if path.exists():  # 防止并发调用在检查后创建同名输出。
            raise FileExistsError(f"{label} 已存在: {path}")  # 禁止覆盖先发布的人工产物。
        os.replace(temporary_path, path)  # 原子发布完整文件。
        temporary_path = None  # 标记临时文件已成为正式文件。
    finally:
        if temporary_path is not None and temporary_path.exists():  # 仅清理未发布的临时文件。
            temporary_path.unlink()  # 避免结果目录堆积碎片。
