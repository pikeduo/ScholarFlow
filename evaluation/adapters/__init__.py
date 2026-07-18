"""导出评测层可替换的本地排序打分边界。"""

from evaluation.adapters.base import OfflineRankingScorer  # 导出不绑定具体模型库的协议。
from evaluation.adapters.prepared_dataset import IMPORT_SCHEMA_VERSION, convert_prepared_dataset_records  # 导出零网络公开数据集准备记录转换器。
from evaluation.adapters.pasa import PASA_AUTOSCHOLARQUERY_SCHEMA_VERSION, convert_pasa_records  # 导出已确认 PaSa 原始 JSONL 转换器。

__all__ = ["IMPORT_SCHEMA_VERSION", "OfflineRankingScorer", "PASA_AUTOSCHOLARQUERY_SCHEMA_VERSION", "convert_pasa_records", "convert_prepared_dataset_records"]
