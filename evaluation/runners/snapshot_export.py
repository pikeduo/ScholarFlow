"""提供必须由用户显式触发的单轮在线候选快照导出边界。"""

import json  # 将已封存快照编码为单行 UTF-8 JSONL。
import os  # 刷新文件缓冲并原子替换同目录临时文件。
from collections.abc import Awaitable, Callable  # 声明可替换候选生成调用边界。
from datetime import datetime, timezone  # 生成包含明确 UTC 时区的快照时间。
from pathlib import Path  # 读取 QueryIntent 并写入用户指定本地路径。
from tempfile import NamedTemporaryFile  # 在输出目录创建可原子替换的临时文件。
from time import perf_counter  # 只测量在线候选生成阶段耗时。
from typing import Protocol  # 声明无需继承生产实现的测试替身协议。

from backend.app.models.candidate_generation import CandidateGenerationResult  # 约束生产候选服务返回值。
from backend.app.models.query_intent import QueryIntent  # 校验用户已经准备好的结构化查询意图。
from evaluation.adapters.scholarflow_snapshot import build_candidate_snapshot  # 将生产内部结果映射为独立评测契约。
from evaluation.contracts.snapshot import CandidateSnapshot  # 返回并写出已封存候选快照。
from evaluation.runners.snapshot_loader import validate_snapshot_integrity  # 写文件前再次核验内容哈希和身份唯一性。


class CandidateGenerator(Protocol):
    """声明快照导出器所需的最小异步候选生成协议。"""

    def generate(self, query: QueryIntent) -> Awaitable[CandidateGenerationResult]:
        """返回规则过滤后、任何模型排序前的候选结果。"""
        ...  # 生产服务和纯测试替身均可实现该协议。


class AllAcademicSourcesFailedError(RuntimeError):
    """表示本次候选生成的全部计划学术来源均失败，因而不能封存评测快照。"""


def load_query_intent(path: Path) -> QueryIntent:
    """以 UTF-8 只读加载用户显式准备的单个 QueryIntent JSON 文件。"""
    payload = path.read_text(encoding="utf-8")  # 不读取 .env、数据库或其他隐式输入。
    return QueryIntent.model_validate_json(payload)  # 使用生产领域契约执行完整字段和冲突校验。


def validate_candidate_generation_result(result: CandidateGenerationResult) -> None:
    """确认候选生成结果至少有一个计划学术来源成功完成。

    参数：
        result：生产候选服务返回的规则过滤后结果。
    异常：
        AllAcademicSourcesFailedError：全部计划学术来源均有安全错误摘要时抛出。
    """
    planned_sources = set(result.route_plan.academic_sources)  # 固化本次路由实际计划调用的学术来源集合。
    failed_sources = set(result.academic_source_errors)  # 读取生产候选服务已记录的失败学术来源集合。
    if planned_sources and planned_sources.issubset(failed_sources):  # 所有计划来源都失败时，零候选不代表可评测的空召回结果。
        raise AllAcademicSourcesFailedError("所有计划学术来源均失败，未写出候选快照")  # 阻止失败审计产物进入后续离线评测。


def validate_snapshot_export_request(query: QueryIntent, *, query_id: str, snapshot_id: str, output_path: Path | None = None) -> None:
    """在任何来源调用前校验单轮、零模型、零网页和输出安全边界。"""
    if not query_id.strip():  # 空白查询标识会使评测数据无法关联。
        raise ValueError("snapshot-export 要求非空 query_id")  # 在调用来源前拒绝无效标识。
    if not snapshot_id.strip():  # 空白快照标识无法形成稳定复用键。
        raise ValueError("snapshot-export 要求非空 snapshot_id")  # 在调用来源前拒绝无效标识。
    if query.retrieval_round != 1:  # 第一版导出器不实现依赖排序反馈的多轮策略。
        raise ValueError("snapshot-export 当前只支持 retrieval_round=1")  # 防止把单轮快照误标为完整多轮候选。
    if query.source_recall_count is None:  # 隐式回退到最终数量会混淆两个参数语义。
        raise ValueError("snapshot-export 要求明确设置 source_recall_count")  # 强制保存真实来源召回规模。
    if query.requires_web_evidence:  # Tavily 发现项不能进入论文候选快照。
        raise ValueError("snapshot-export 要求 requires_web_evidence=false")  # 在路由前阻止网页来源调用。
    if query.enable_semantic_ranking:  # 候选导出不应声明将执行 BGE-M3。
        raise ValueError("snapshot-export 要求 enable_semantic_ranking=false")  # 保持冻结意图与零本地模型行为一致。
    if query.enable_cross_encoder_ranking:  # 候选导出不应声明将执行 Cross Encoder。
        raise ValueError("snapshot-export 要求 enable_cross_encoder_ranking=false")  # 保持冻结意图与导出边界一致。
    if output_path is not None and output_path.exists():  # 已有快照不得被一次在线调用静默覆盖。
        raise FileExistsError(f"候选快照输出已存在: {output_path}")  # 让用户显式选择新的输出路径或自行处理旧文件。


async def export_candidate_snapshot(
    generator: CandidateGenerator,
    query: QueryIntent,
    *,
    query_id: str,
    snapshot_id: str,
    created_at: datetime | None = None,
    clock: Callable[[], float] = perf_counter,
) -> CandidateSnapshot:
    """执行一次候选生成并返回已封存快照，不运行任何后续排序阶段。

    参数：
        generator：通常为生产 CandidateGenerationService，也可为离线测试替身。
        query：用户已准备且显式关闭网页与本地模型的单轮 QueryIntent。
        query_id：评测数据集查询标识。
        snapshot_id：本次候选快照唯一标识。
        created_at：测试可注入的带时区创建时间。
        clock：测试可注入的单调时钟。
    返回：
        CandidateSnapshot：包含在线候选、阶段统计、usage 和 SHA-256 的快照。
    异常：
        AllAcademicSourcesFailedError：全部计划学术来源失败时抛出且不构造快照。
    """
    validate_snapshot_export_request(query, query_id=query_id, snapshot_id=snapshot_id)  # 在任何来源调用前检查安全边界。
    started_at = clock()  # 记录候选服务调用前的单调时间。
    result = await generator.generate(query)  # 只调用到规则过滤后的共享生产候选边界。
    latency_ms = max(0.0, (clock() - started_at) * 1000.0)  # 转换为非负毫秒并排除本地文件写入耗时。
    validate_candidate_generation_result(result)  # 全部计划学术来源失败时拒绝把失败产物封存为零候选快照。
    return build_candidate_snapshot(result, query_id=query_id, snapshot_id=snapshot_id, latency_ms=latency_ms, created_at=created_at or datetime.now(timezone.utc))  # 映射并封存快照。


async def export_candidate_snapshot_to_file(
    generator: CandidateGenerator,
    query: QueryIntent,
    *,
    query_id: str,
    snapshot_id: str,
    output_path: Path,
    created_at: datetime | None = None,
    clock: Callable[[], float] = perf_counter,
) -> CandidateSnapshot:
    """预检输出后执行一次候选生成，并将单条快照安全写入新 JSONL 文件。"""
    validate_snapshot_export_request(query, query_id=query_id, snapshot_id=snapshot_id, output_path=output_path)  # 避免调用来源后才发现目标冲突。
    snapshot = await export_candidate_snapshot(generator, query, query_id=query_id, snapshot_id=snapshot_id, created_at=created_at, clock=clock)  # 执行唯一一次在线候选生成。
    write_candidate_snapshot(snapshot, output_path)  # 将已封存快照写入用户指定的新文件。
    return snapshot  # 返回写入内容供 CLI 输出安全计数。


def write_candidate_snapshot(snapshot: CandidateSnapshot, output_path: Path) -> None:
    """将一份已封存快照通过同目录临时文件原子写入新 JSONL 文件。"""
    validate_snapshot_integrity(snapshot)  # 写入前核验哈希和论文身份唯一性。
    if output_path.exists():  # 再次检查以缩小在线生成期间目标被创建的竞态窗口。
        raise FileExistsError(f"候选快照输出已存在: {output_path}")  # 禁止静默覆盖用户已有评测输入。
    output_path.parent.mkdir(parents=True, exist_ok=True)  # 仅创建用户显式指定输出路径的父目录。
    serialized = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n"  # 编码为单条紧凑 UTF-8 JSONL。
    temporary_path: Path | None = None  # 保存临时路径以便异常时清理。
    try:  # 写入、刷新和替换任一步失败都不得留下临时文件。
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp", delete=False) as stream:  # 在同一文件系统创建临时文件以支持原子替换。
            temporary_path = Path(stream.name)  # 保存关闭后可用于替换的临时路径。
            stream.write(serialized)  # 写入恰好一条候选快照记录。
            stream.flush()  # 将 Python 缓冲区刷新到底层文件描述符。
            os.fsync(stream.fileno())  # 请求操作系统落盘后再暴露最终路径。
        if output_path.exists():  # 替换前再次拒绝覆盖并缩小并发创建窗口。
            raise FileExistsError(f"候选快照输出已存在: {output_path}")  # 保留已存在目标并清理临时文件。
        os.replace(temporary_path, output_path)  # 在同目录原子发布完整 JSONL 文件。
        temporary_path = None  # 标记临时路径已经成为最终输出，避免清理新文件。
    finally:
        if temporary_path is not None and temporary_path.exists():  # 仅清理尚未发布的临时文件。
            temporary_path.unlink()  # 避免失败的在线导出在结果目录留下碎片。
