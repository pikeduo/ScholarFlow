"""ScholarWeave 的领域数据模型包。"""

from backend.app.models.discovery import SupplementalDiscoveryItem  # 对外导出不可合并的补充网页发现模型。
from backend.app.models.query import QuerySchema  # 对外导出结构化查询模型。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 对外导出完整查询规划契约。
from backend.app.models.paper import Paper, PaperAuthor, PaperRecord, PaperSourceRecord  # 对外导出基础和多源论文模型。
from backend.app.models.search import SearchResult  # 对外导出检索阶段结果模型。
from backend.app.models.search_run import SearchRunState  # 对外导出可恢复搜索运行状态。

__all__ = ["Paper", "PaperAuthor", "PaperRecord", "PaperSourceRecord", "QueryIntent", "QuerySchema", "QuerySubquery", "SearchResult", "SearchRunState", "SupplementalDiscoveryItem"]  # 限制包级导出的公共模型。
