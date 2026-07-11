"""ScholarWeave 外部学术数据源适配器包。"""

from backend.app.adapters.base import AcademicSearchAdapter  # 对外导出统一来源适配器协议。
from backend.app.adapters.arxiv import ArxivClient, build_arxiv_search_params, map_arxiv_entry  # 对外导出 arXiv 适配器接口。
from backend.app.adapters.openalex import OpenAlexClient, build_openalex_search_params, build_openalex_work_params, map_openalex_work_to_paper, map_openalex_work_to_record  # 对外导出 OpenAlex 适配器接口。
from backend.app.adapters.semantic_scholar import SemanticScholarClient, build_semantic_scholar_search_params, map_semantic_scholar_paper  # 对外导出 Semantic Scholar 搜索适配器接口。

__all__ = ["AcademicSearchAdapter", "ArxivClient", "OpenAlexClient", "SemanticScholarClient", "build_arxiv_search_params", "build_openalex_search_params", "build_openalex_work_params", "build_semantic_scholar_search_params", "map_arxiv_entry", "map_openalex_work_to_paper", "map_openalex_work_to_record", "map_semantic_scholar_paper"]  # 限制适配器包的公共接口。
