"""ScholarFlow 查询、检索、去重和排序业务服务包。"""
"""ScholarWeave 检索、融合与排序业务服务包。"""

from backend.app.services.multi_source_recall import MultiSourceRecallCoordinator  # 对外导出多源召回协调服务。
from backend.app.services.cross_encoder_ranking import CrossEncoderReranker  # 对外导出 Cross Encoder 精细重排服务。
from backend.app.services.llm_ranking import LlmPaperReranker  # 对外导出 LLM 约束核验与最终精排服务。
from backend.app.services.multi_source_filtering import MultiSourcePaperFilter  # 对外导出多源规则过滤服务。
from backend.app.services.paper_fusion import PaperFusionService  # 对外导出跨来源论文融合服务。
from backend.app.services.semantic_ranking import SemanticRanker  # 对外导出 BGE-M3 语义粗排服务。
from backend.app.services.source_router import SourceRouter  # 对外导出动态来源路由服务。

__all__ = ["CrossEncoderReranker", "LlmPaperReranker", "MultiSourcePaperFilter", "MultiSourceRecallCoordinator", "PaperFusionService", "SemanticRanker", "SourceRouter"]  # 限制服务包的公共接口。
