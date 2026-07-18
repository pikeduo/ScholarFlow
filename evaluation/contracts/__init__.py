"""导出离线评测使用的稳定数据契约。"""

from evaluation.contracts.common import EvaluationPaper, EvaluationUsage, RelationRecord  # 复用论文、用量与关系契约。
from evaluation.contracts.gold import GoldQuery  # 导出金标查询契约。
from evaluation.contracts.prediction import PredictionRecord, RankingConfig  # 导出预测和排序配置契约。
from evaluation.contracts.result import EvaluationSummary  # 导出完整评测报告契约。

__all__ = ["EvaluationPaper", "EvaluationUsage", "EvaluationSummary", "GoldQuery", "PredictionRecord", "RankingConfig", "RelationRecord"]  # 明确评测层公共模型。
