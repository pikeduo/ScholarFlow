"""ScholarFlow 查询、检索、去重和排序业务服务包。"""
"""ScholarWeave 检索、融合与排序业务服务包。"""

from backend.app.services.multi_source_recall import MultiSourceRecallCoordinator  # 对外导出多源召回协调服务。
from backend.app.services.multi_source_filtering import MultiSourcePaperFilter  # 对外导出多源规则过滤服务。
from backend.app.services.paper_fusion import PaperFusionService  # 对外导出跨来源论文融合服务。
from backend.app.services.source_router import SourceRouter  # 对外导出动态来源路由服务。

__all__ = ["MultiSourcePaperFilter", "MultiSourceRecallCoordinator", "PaperFusionService", "SourceRouter"]  # 限制服务包的公共接口。
