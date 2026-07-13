"""ScholarFlow 查询、检索、去重和排序业务服务包。"""
"""ScholarWeave 检索、融合与排序业务服务包。"""

from backend.app.services.multi_source_recall import MultiSourceRecallCoordinator  # 对外导出多源召回协调服务。
from backend.app.services.multi_round_search import MultiRoundSearchController  # 对外导出多轮搜索控制服务。
from backend.app.services.coverage_analysis import CoverageGapAnalyzer  # 对外导出覆盖缺口分析服务。
from backend.app.services.cross_encoder_ranking import CrossEncoderReranker  # 对外导出 Cross Encoder 精细重排服务。
from backend.app.services.llm_ranking import LlmPaperReranker  # 对外导出 LLM 约束核验与最终精排服务。
from backend.app.services.multi_source_filtering import MultiSourcePaperFilter  # 对外导出多源规则过滤服务。
from backend.app.services.paper_fusion import PaperFusionService  # 对外导出跨来源论文融合服务。
from backend.app.services.query_planning import QueryPlanningService  # 对外导出自然语言查询规划服务。
from backend.app.services.query_evolution import QueryEvolutionService  # 对外导出覆盖缺口驱动的查询演化服务。
from backend.app.services.semantic_ranking import SemanticRanker  # 对外导出 BGE-M3 语义粗排服务。
from backend.app.services.search_run_store import SearchRunStateStore, SqliteSearchRunStateStore  # 对外导出可替换的搜索运行状态存储边界。
from backend.app.services.search_events import InMemorySearchRunEventPublisher, SearchRunEventPublisher  # 对外导出 SSE 进度事件发布边界与内存实现。
from backend.app.services.paper_text import BuiltText, PaperTextBuilder, PaperTextBuilderError  # 对外导出统一文本构造与哈希边界。
from backend.app.services.embedding import EmbeddingBatch, EmbeddingService, EmbeddingServiceConfig, EmbeddingServiceError  # 对外导出批量嵌入服务与稳定结果契约。
from backend.app.services.library_vector_index import LibraryVectorIndexResult, LibraryVectorIndexer  # 对外导出文献库首次语义检索前的延迟向量写入编排器。
from backend.app.services.library_semantic_search import LibrarySemanticSearchService  # 对外导出文献库自然语言语义检索服务。
from backend.app.services.source_router import SourceRouter  # 对外导出动态来源路由服务。

__all__ = ["BuiltText", "CoverageGapAnalyzer", "CrossEncoderReranker", "EmbeddingBatch", "EmbeddingService", "EmbeddingServiceConfig", "EmbeddingServiceError", "InMemorySearchRunEventPublisher", "LibrarySemanticSearchService", "LibraryVectorIndexResult", "LibraryVectorIndexer", "LlmPaperReranker", "MultiRoundSearchController", "MultiSourcePaperFilter", "MultiSourceRecallCoordinator", "PaperFusionService", "PaperTextBuilder", "PaperTextBuilderError", "QueryEvolutionService", "QueryPlanningService", "SearchRunEventPublisher", "SearchRunStateStore", "SemanticRanker", "SourceRouter", "SqliteSearchRunStateStore"]  # 限制服务包的公共接口。
