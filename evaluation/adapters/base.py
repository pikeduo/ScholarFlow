"""定义不加载模型的离线排序适配器协议。"""

from collections.abc import Mapping, Sequence  # 只向打分器暴露只读语义接口。
from typing import Protocol  # 允许测试替身和未来模型适配器实现统一边界。

from evaluation.contracts.ablation import RankingScoreBatch  # 统一打分器返回契约。
from evaluation.contracts.snapshot import CandidatePaper  # 使用排序前候选论文。


class OfflineRankingScorer(Protocol):
    """声明 BGE-M3 或 Cross Encoder 离线打分器的最小可替换接口。"""

    def score(self, query: str, query_intent: Mapping[str, object], papers: Sequence[CandidatePaper]) -> RankingScoreBatch:
        """为输入候选按原顺序返回等长分数和本地运行统计。"""
        ...  # 协议不包含模型加载、下载或生产服务依赖。
