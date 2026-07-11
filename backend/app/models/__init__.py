"""ScholarFlow 的领域数据模型包。"""

from backend.app.models.query import QuerySchema  # 对外导出结构化查询模型。
from backend.app.models.paper import Paper, PaperAuthor  # 对外导出统一论文和作者模型。

__all__ = ["Paper", "PaperAuthor", "QuerySchema"]  # 限制包级导出的公共模型。
