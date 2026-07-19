"""导出完全离线评测、数据集金标导入与候选快照运行入口。"""

from evaluation.runners.fixture import EvaluationRunConfig, evaluate_records, run_fixture  # 导出纯内存与文件运行入口。
from evaluation.runners.dataset_import import import_prepared_dataset_gold, write_gold_queries  # 导出用户手动准备数据集金标的完全离线导入入口。
from evaluation.runners.offline_ranking import build_ablation_plan, load_ablation_matrix, run_ablation_matrix, run_offline_experiment, write_ablation_plan  # 导出离线消融计划与运行入口。
from evaluation.runners.offline_execution import execute_ablation_to_files  # 导出计划内本地排序执行与原子归档入口。
from evaluation.runners.pasa_import import import_pasa_gold  # 导出已下载 PaSa 原始 JSONL 的完全离线转换入口。
from evaluation.runners.snapshot_loader import load_candidate_snapshots, validate_snapshot_integrity  # 导出只读快照加载与完整性校验。
from evaluation.runners.gold_subset import select_gold_subset, select_gold_subset_to_files  # 导出开发集 GoldQuery 子集的完全离线封存入口。
from evaluation.runners.snapshot_collection import assemble_candidate_snapshot_collection, parse_snapshot_overrides  # 导出多份单查询快照的完全离线集合组装入口。

__all__ = ["EvaluationRunConfig", "assemble_candidate_snapshot_collection", "build_ablation_plan", "evaluate_records", "execute_ablation_to_files", "import_pasa_gold", "import_prepared_dataset_gold", "load_ablation_matrix", "load_candidate_snapshots", "parse_snapshot_overrides", "run_ablation_matrix", "run_fixture", "run_offline_experiment", "select_gold_subset", "select_gold_subset_to_files", "validate_snapshot_integrity", "write_ablation_plan", "write_gold_queries"]
