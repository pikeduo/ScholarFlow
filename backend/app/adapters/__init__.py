"""ScholarWeave 外部学术数据源适配器包。"""

from backend.app.adapters.base import AcademicSearchAdapter, WebDiscoveryAdapter  # 对外导出学术来源与补充网页发现协议。
from backend.app.adapters.bge_m3 import BgeM3Encoder, BgeM3EncoderError, BgeM3OutOfMemoryError, DenseTextEncoder, EmbeddingDeviceResolver, SemanticTextEncoder, TorchEmbeddingDeviceResolver  # 对外导出 BGE-M3 语义编码与设备选择边界。
from backend.app.adapters.cross_encoder import BgeCrossEncoder, CrossEncoderError, CrossEncoderScorer  # 对外导出 Cross Encoder 重排边界。
from backend.app.adapters.deepseek_llm import DeepSeekPaperAssessmentClient, LlmAssessmentError, PaperAssessmentClient  # 对外导出 DeepSeek 与可替换 LLM 核验边界。
from backend.app.adapters.deepseek_query_planner import DeepSeekQueryPlanningClient, QueryPlanningClient, QueryPlanningError  # 对外导出自然语言查询规划边界。
from backend.app.adapters.arxiv import ArxivClient, build_arxiv_search_params, map_arxiv_entry  # 对外导出 arXiv 适配器接口。
from backend.app.adapters.dblp import DblpClient, build_dblp_search_params, map_dblp_hit  # 对外导出 DBLP 适配器接口。
from backend.app.adapters.openalex import OpenAlexClient, build_openalex_search_params, build_openalex_work_params, map_openalex_work_to_paper, map_openalex_work_to_record  # 对外导出 OpenAlex 适配器接口。
from backend.app.adapters.pubmed import PubMedClient, build_pubmed_esearch_params, map_pubmed_article  # 对外导出 PubMed E-utilities 适配器接口。
from backend.app.adapters.semantic_scholar import SemanticScholarClient, build_semantic_scholar_search_params, map_semantic_scholar_paper  # 对外导出 Semantic Scholar 搜索适配器接口。
from backend.app.adapters.tavily import TavilyClient, build_tavily_search_payload, map_tavily_result  # 对外导出 Tavily 补充发现接口。

__all__ = ["AcademicSearchAdapter", "ArxivClient", "BgeCrossEncoder", "BgeM3Encoder", "BgeM3EncoderError", "BgeM3OutOfMemoryError", "CrossEncoderError", "CrossEncoderScorer", "DeepSeekPaperAssessmentClient", "DeepSeekQueryPlanningClient", "DenseTextEncoder", "DblpClient", "EmbeddingDeviceResolver", "LlmAssessmentError", "OpenAlexClient", "PaperAssessmentClient", "PubMedClient", "QueryPlanningClient", "QueryPlanningError", "SemanticScholarClient", "SemanticTextEncoder", "TavilyClient", "TorchEmbeddingDeviceResolver", "WebDiscoveryAdapter", "build_arxiv_search_params", "build_dblp_search_params", "build_openalex_search_params", "build_openalex_work_params", "build_pubmed_esearch_params", "build_semantic_scholar_search_params", "build_tavily_search_payload", "map_arxiv_entry", "map_dblp_hit", "map_openalex_work_to_paper", "map_openalex_work_to_record", "map_pubmed_article", "map_semantic_scholar_paper", "map_tavily_result"]  # 限制适配器包的公共接口。
