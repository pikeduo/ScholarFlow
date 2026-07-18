"""导出离线评测使用的稳定数据契约。"""

from evaluation.contracts.common import EvaluationPaper, EvaluationUsage, RelationRecord  # 复用论文、用量与关系契约。
from evaluation.contracts.dataset import PreparedDatasetGoldRecord  # 导出用户本地准备数据集金标输入契约。
from evaluation.contracts.ablation import AblationExperiment, AblationMatrix, AblationPlan, OfflineAblationResult, RankingScoreBatch, RankingStageTrace, build_standard_ablation_matrix  # 导出消融配置、标准矩阵与结果契约。
from evaluation.contracts.gold import GoldQuery  # 导出金标查询契约。
from evaluation.contracts.prediction import PredictionRecord, RankingConfig  # 导出预测和排序配置契约。
from evaluation.contracts.result import EvaluationSummary  # 导出完整评测报告契约。
from evaluation.contracts.snapshot import CandidatePaper, CandidateSnapshot, CandidateSourceRecord, compute_snapshot_hash, seal_snapshot  # 导出候选快照契约与哈希入口。

__all__ = ["AblationExperiment", "AblationMatrix", "AblationPlan", "CandidatePaper", "CandidateSnapshot", "CandidateSourceRecord", "EvaluationPaper", "EvaluationUsage", "EvaluationSummary", "GoldQuery", "OfflineAblationResult", "PredictionRecord", "PreparedDatasetGoldRecord", "RankingConfig", "RankingScoreBatch", "RankingStageTrace", "RelationRecord", "build_standard_ablation_matrix", "compute_snapshot_hash", "seal_snapshot"]  # 明确评测层公共模型。
