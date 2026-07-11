"""ScholarWeave 的领域数据模型包。"""

from backend.app.models.discovery import SupplementalDiscoveryItem  # 对外导出不可合并的补充网页发现模型。
from backend.app.models.cross_encoder_ranking import CrossEncoderRankingResult  # 对外导出 Cross Encoder 重排结果模型。
from backend.app.models.llm_ranking import LlmAssessmentBatch, LlmPaperAssessment, LlmRankingResult  # 对外导出 LLM 核验与最终精排契约。
from backend.app.models.query import QuerySchema  # 对外导出结构化查询模型。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 对外导出完整查询规划契约。
from backend.app.models.paper import Paper, PaperAuthor, PaperRecord, PaperSourceRecord  # 对外导出基础和多源论文模型。
from backend.app.models.paper_fusion import PaperFusionResult  # 对外导出跨来源论文融合结果模型。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 对外导出多源召回协调结果模型。
from backend.app.models.multi_source_filtering import MultiSourceFilterResult  # 对外导出多源规则过滤结果模型。
from backend.app.models.search import SearchResult  # 对外导出检索阶段结果模型。
from backend.app.models.search_run import SearchRunState  # 对外导出可恢复搜索运行状态。
from backend.app.models.semantic_ranking import SemanticRankingResult  # 对外导出语义粗排结果模型。
from backend.app.models.source_routing import SourceRoutePlan  # 对外导出来源选择与降级计划模型。

__all__ = ["CrossEncoderRankingResult", "LlmAssessmentBatch", "LlmPaperAssessment", "LlmRankingResult", "MultiSourceFilterResult", "MultiSourceRecallResult", "Paper", "PaperAuthor", "PaperFusionResult", "PaperRecord", "PaperSourceRecord", "QueryIntent", "QuerySchema", "QuerySubquery", "SearchResult", "SearchRunState", "SemanticRankingResult", "SourceRoutePlan", "SupplementalDiscoveryItem"]  # 限制包级导出的公共模型。
