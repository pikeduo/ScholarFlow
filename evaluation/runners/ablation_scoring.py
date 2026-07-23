"""将已归档消融结果确定性转换为预测，并按实验生成离线报告。"""

import hashlib  # 核验执行归档与结果 JSONL 的原始字节哈希。
import json  # 读写 UTF-8 JSONL 与评分 manifest。
import os  # 原子发布完整评分目录。
import shutil  # 仅清理本函数创建且未发布的临时目录。
from pathlib import Path  # 处理用户显式提供的本地路径。
from tempfile import mkdtemp  # 在正式目录旁创建临时评分目录。

from evaluation.contracts.ablation import OfflineAblationResult  # 解析一行一个的已归档实验结果。
from evaluation.contracts.gold import GoldQuery  # 读取已封存的金标查询。
from evaluation.contracts.offline_run import OfflineRankingRunManifest  # 核验结果确属已审核的执行归档。
from evaluation.runners.fixture import EvaluationRunConfig, evaluate_records, load_jsonl, load_run_config  # 复用既有离线指标与代理分实现。
from evaluation.reports.writers import write_reports  # 为每个实验写入既有三种报告格式。


def score_ablation_results(*, results_path: Path, run_manifest_path: Path, gold_path: Path, config_path: Path | None, output_dir: Path) -> dict[str, object]:
    """只读评分已归档结果；按实验写预测和报告，不加载模型或访问网络。"""
    if output_dir.exists():  # 已有目录可能对应已审阅报告，绝不覆盖。
        raise FileExistsError(f"消融评分输出目录已存在: {output_dir}")  # 要求用户使用新版本目录。
    result_bytes = results_path.read_bytes()  # 读取用户已完成的本地执行产物。
    manifest = _load_run_manifest(run_manifest_path)  # 读取配套执行 manifest。
    if hashlib.sha256(result_bytes).hexdigest() != manifest.result_sha256:  # 结果修改后不得继续评分。
        raise ValueError("离线排序结果 SHA-256 与执行 manifest 不一致")  # 阻止手工替换或截断结果。
    results = _load_results(results_path)  # 解析全部离线实验结果。
    gold_queries = load_jsonl(gold_path, GoldQuery)  # 只读加载现有 GoldQuery 子集。
    config = load_run_config(config_path)  # 只读取 Top-K 与代理分配置。
    grouped = _group_results(results, manifest, gold_queries)  # 校验实验、查询和 manifest 完整对应。
    temporary_dir = Path(mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))  # 在同一文件系统构建完整临时报告。
    try:  # 任何评分或写入失败都不发布正式目录。
        experiment_reports: dict[str, dict[str, object]] = {}  # 保存各实验可审计的摘要和相对路径。
        for experiment_id, predictions in grouped.items():  # 按 manifest 冻结的实验顺序逐组评分。
            experiment_dir = temporary_dir / experiment_id  # 每个实验独占预测和报告目录。
            experiment_dir.mkdir()  # 避免不同配置互相覆盖报告文件名。
            prediction_path = experiment_dir / "predictions.jsonl"  # 固定可供后续复评分的预测归档名。
            prediction_path.write_text("".join(prediction.model_dump_json() + "\n" for prediction in predictions), encoding="utf-8")  # 按 GoldQuery 顺序写预测。
            summary = evaluate_records(gold_queries, predictions, config)  # 完全离线计算既有检索、效率和结构代理分。
            write_reports(summary, experiment_dir)  # 写入 report.json、query_metrics.jsonl 与 report.md。
            experiment_reports[experiment_id] = {"prediction_path": f"{experiment_id}/predictions.jsonl", "report_path": f"{experiment_id}/report.json", "query_count": summary.retrieval.query_count}  # 记录相对路径而不泄露机器目录。
        score_manifest = {"schema_version": "ablation-score-manifest-v1", "results_sha256": manifest.result_sha256, "run_id": manifest.run_id, "gold_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(), "experiment_ids": list(grouped), "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path is not None else None, "experiments": experiment_reports, "academic_api_calls": 0, "deepseek_calls": 0, "local_model_calls": 0}  # 评分阶段明确不新增任何外部或模型调用。
        (temporary_dir / "score-manifest.json").write_text(json.dumps(score_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")  # 写入本次评分输入与输出映射。
        os.replace(temporary_dir, output_dir)  # 只在全部实验报告成功后原子发布整个目录。
    except Exception:
        _remove_tree(temporary_dir)  # 失败不保留半成品评分报告。
        raise
    return score_manifest  # 返回 CLI 仅需展示的实验数和零调用边界。


def _load_run_manifest(path: Path) -> OfflineRankingRunManifest:
    """解析已有执行 manifest，拒绝损坏或不兼容输入。"""
    try:
        return OfflineRankingRunManifest.model_validate_json(path.read_text(encoding="utf-8"))  # 只读校验固定归档契约。
    except Exception as error:
        raise ValueError(f"离线执行 manifest 无效: {path}") from error  # 不回显运行结果正文。


def _load_results(path: Path) -> list[OfflineAblationResult]:
    """以 UTF-8 逐行加载已归档结果，拒绝空文件和损坏记录。"""
    results: list[OfflineAblationResult] = []  # 保留文件的稳定任务顺序。
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):  # 遍历 JSONL 各行。
        if not line.strip():
            continue  # 忽略空白行。
        try:
            results.append(OfflineAblationResult.model_validate_json(line))  # 校验完整预测与阶段审计。
        except Exception as error:
            raise ValueError(f"离线排序结果第 {line_number} 行无效") from error  # 返回不含论文正文的定位信息。
    if not results:
        raise ValueError("离线排序结果不包含有效记录")  # 避免输出空报告。
    return results  # 返回通过契约校验的结果列表。


def _group_results(results: list[OfflineAblationResult], manifest: OfflineRankingRunManifest, gold_queries: list[GoldQuery]) -> dict[str, list]:
    """按实验冻结顺序分组，并要求每组与 GoldQuery 查询集合精确一致。"""
    grouped: dict[str, list] = {experiment_id: [] for experiment_id in manifest.selected_experiment_ids}  # 按执行 manifest 保持稳定实验顺序。
    for result in results:
        if result.experiment_id not in grouped or result.matrix_id != manifest.matrix_id or result.snapshot_id not in manifest.snapshot_ids:  # 结果必须来自同一次归档。
            raise ValueError("离线排序结果与执行 manifest 不一致")  # 拒绝拼接其他运行的 JSONL。
        grouped[result.experiment_id].append(result.prediction)  # 仅提取可复用的 PredictionRecord。
    gold_ids = [gold.query_id for gold in gold_queries]  # 读取评分分母的稳定顺序。
    if len(set(gold_ids)) != len(gold_ids):
        raise ValueError("GoldQuery 包含重复 query_id")  # 复用 fixture 前先明确分母错误。
    for experiment_id, predictions in grouped.items():
        prediction_ids = [prediction.query_id for prediction in predictions]  # 收集当前配置预测集合。
        if len(set(prediction_ids)) != len(prediction_ids) or set(prediction_ids) != set(gold_ids):  # 每组都必须完整一对一覆盖金标。
            raise ValueError(f"experiment {experiment_id} 的预测与 GoldQuery 查询集合不一致")  # 不允许部分结果伪装为完整对比。
        grouped[experiment_id] = [next(prediction for prediction in predictions if prediction.query_id == query_id) for query_id in gold_ids]  # 按 GoldQuery 顺序重排，保证报告稳定。
    return grouped  # 返回可直接交给既有评分器的预测列表。


def _remove_tree(path: Path) -> None:
    """仅清理本函数刚创建且尚未发布的临时评分目录。"""
    if not path.exists():
        return  # 目录已发布或不存在时无需处理。
    shutil.rmtree(path)  # 目标仅可能是本函数通过 mkdtemp 创建且尚未发布的临时目录。
