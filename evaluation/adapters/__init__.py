"""导出评测层可替换的本地排序打分边界。"""

from evaluation.adapters.base import OfflineRankingScorer  # 导出不绑定具体模型库的协议。

__all__ = ["OfflineRankingScorer"]
