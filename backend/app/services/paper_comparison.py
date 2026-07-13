"""将已保存论文投影为不调用模型或外部来源的事实型对比结果。"""

from collections.abc import Sequence  # 标注小集合论文输入序列。

from backend.app.models.comparison import ComparePapersResponse, PaperComparisonItem  # 使用稳定对比响应契约。
from backend.app.models.paper import PaperRecord  # 接收 SQLite 已恢复的规范化论文事实。


class PaperComparisonService:
    """负责将二至五篇论文转换为可核验的固定列对比结果。"""

    def compare(self, papers: Sequence[PaperRecord]) -> ComparePapersResponse:
        """复用现有元数据与核验证据生成对比，不调用 PDF、LLM 或外部 API。

        参数：
            papers：已按用户选择顺序恢复的完整规范化论文。
        返回：
            ComparePapersResponse：按原顺序排列的事实型比较列。
        """
        return ComparePapersResponse(items=[PaperComparisonItem.from_paper(paper) for paper in papers])  # 逐篇投影并保持用户选择顺序。
