"""在已保存多轮搜索结果上提供确定性的筛选、排序和分页服务。"""

from math import ceil  # 计算稳定的总页数。

from backend.app.models.multi_round_search import MultiRoundSearchResult  # 读取同次运行的完整最终结果快照。
from backend.app.models.paper import PaperRecord  # 使用已规范化的论文事实进行筛选和排序。
from backend.app.models.search_result_page import SearchResultRelevance, SearchResultSort, SearchRunPaperPage  # 返回公开分页契约。


class SearchResultPageService:
    """仅处理持久化结果快照的筛选、排序和分页，不执行任何检索。

    相关性排序会从快照中的分层分数重新计算稳定展示顺序，确保历史运行也遵守
    “相关性优先、约束状态仅作同分决策”的规则；不会写回 SQLite、改写模型分数或触发学术来源调用。
    """

    def build_page(
        self,
        result: MultiRoundSearchResult,
        *,
        source: str | None,
        relevance: SearchResultRelevance | None,
        year_start: int | None,
        year_end: int | None,
        sort: SearchResultSort,
        page: int,
        page_size: int,
    ) -> SearchRunPaperPage:
        """从同次完成结果构造一页稳定论文响应。

        参数：
            result：已从 SQLite 读取的同次最终搜索结果。
            source：可选来源筛选，空值表示不过滤。
            relevance：可选约束核验状态筛选，空值表示不过滤。
            year_start：可选发表年份下界，论文缺失年份时不匹配。
            year_end：可选发表年份上界，论文缺失年份时不匹配。
            sort：明确允许的展示排序策略。
            page：已由 API 参数校验的从一开始的页码。
            page_size：已由 API 参数校验的每页论文数量。
        返回：
            SearchRunPaperPage：仅来自持久化结果快照的分页响应。
        """
        filtered_papers = self._filter_papers(  # 先执行确定性条件过滤，再计算总数和分页边界。
            result.papers,
            source=source,
            relevance=relevance,
            year_start=year_start,
            year_end=year_end,
        )
        sorted_papers = self._sort_papers(filtered_papers, sort)  # 仅改变展示顺序，不修改论文对象或其分数。
        total = len(sorted_papers)  # 记录筛选后总数供前端展示与页码校正。
        total_pages = max(1, ceil(total / page_size))  # 空结果也保留第一页，避免前端出现零页状态。
        safe_page = min(page, total_pages)  # 当筛选变化导致页码越界时返回最后一个有效页。
        offset = (safe_page - 1) * page_size  # 计算当前页在稳定排序结果中的起始偏移。
        return SearchRunPaperPage(  # 返回未修改的规范化论文记录和服务端分页元数据。
            run_id=result.run_state.run_id,
            items=sorted_papers[offset : offset + page_size],
            total=total,
            page=safe_page,
            page_size=page_size,
            total_pages=total_pages,
        )

    def _filter_papers(
        self,
        papers: list[PaperRecord],
        *,
        source: str | None,
        relevance: SearchResultRelevance | None,
        year_start: int | None,
        year_end: int | None,
    ) -> list[PaperRecord]:
        """按公开筛选字段保留论文，并保持原结果顺序。

        返回：
            list[PaperRecord]：满足全部已设置条件的论文列表。
        """
        return [  # 使用列表推导保留同次搜索结果的稳定相对顺序。
            paper  # 仅返回原始不可变论文对象，不复制或补充外部字段。
            for paper in papers  # 按最终结果快照的既有相关性顺序遍历。
            if (source is None or paper.source == source)  # 来源筛选只匹配最终记录实际来源。
            and (relevance is None or paper.constraint_status == relevance)  # 核验状态使用已有证据守卫结果。
            and (year_start is None or (paper.year is not None and paper.year >= year_start))  # 缺失年份不满足显式下界。
            and (year_end is None or (paper.year is not None and paper.year <= year_end))  # 缺失年份不满足显式上界。
        ]

    def _sort_papers(self, papers: list[PaperRecord], sort: SearchResultSort) -> list[PaperRecord]:
        """按公开排序选项返回新列表，相关性模式使用已保存分数稳定重排。

        返回：
            list[PaperRecord]：已按展示策略排序的论文副本。
        """
        if sort == "relevance":  # 历史快照也必须纠正旧版“约束状态优先”的展示顺序。
            return sorted(papers, key=_relevance_key)  # 仅使用已保存分数重排，不重新调用模型或来源。
        if sort == "year_desc":  # 用户要求最新优先时按年份倒序排列。
            return sorted(papers, key=lambda paper: (paper.year is not None, paper.year or 0, paper.paper_id), reverse=True)  # 缺失年份稳定置后并以标识打破并列。
        return sorted(papers, key=lambda paper: (paper.citation_count, paper.year or 0, paper.paper_id), reverse=True)  # 引用量排序时以年份和标识提供确定性并列顺序。


def _relevance_key(paper: PaperRecord) -> tuple[float, float, float, float, float, str]:
    """构造与最终精排一致的展示排序键，使相关性始终高于约束展示状态。"""
    status_priority = 0.0 if paper.constraint_status == "satisfied" else 1.0  # 仅在全部相关性分数并列时优先可验证约束。
    return (-_score_or_negative_infinity(paper.llm_relevance_score), -_score_or_negative_infinity(paper.cross_encoder_score), -_score_or_negative_infinity(paper.semantic_score), -paper.rrf_score, status_priority, paper.paper_id)  # 使用快照已有分数保证历史和新运行的排序规则一致。


def _score_or_negative_infinity(score: float | None) -> float:
    """将缺失相关性分数放在已知分数之后，保持降级运行的确定性。"""
    return score if score is not None else float("-inf")  # 缺失模型分数时由后续层次和标识稳定排序。
