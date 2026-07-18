"""导出完全离线评测、数据集金标导入与候选快照运行入口。"""

from evaluation.runners.fixture import EvaluationRunConfig, evaluate_records, run_fixture  # 导出纯内存与文件运行入口。
from evaluation.runners.dataset_import import import_prepared_dataset_gold, write_gold_queries  # 导出用户手动准备数据集金标的完全离线导入入口。
from evaluation.runners.offline_ranking import build_ablation_plan, load_ablation_matrix, run_ablation_matrix, run_offline_experiment, write_ablation_plan  # 导出离线消融计划与运行入口。
from evaluation.runners.snapshot_loader import load_candidate_snapshots, validate_snapshot_integrity  # 导出只读快照加载与完整性校验。

__all__ = ["EvaluationRunConfig", "build_ablation_plan", "evaluate_records", "import_prepared_dataset_gold", "load_ablation_matrix", "load_candidate_snapshots", "run_ablation_matrix", "run_fixture", "run_offline_experiment", "validate_snapshot_integrity", "write_ablation_plan", "write_gold_queries"]
