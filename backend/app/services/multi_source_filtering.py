"""在多源融合论文上执行 QueryIntent 的确定性规则过滤。"""

from collections import Counter  # 汇总每篇论文首个不满足规则的可观测原因。

from backend.app.core.logging import logger  # 记录不包含查询文本和论文正文的过滤统计。
from backend.app.models.multi_source_filtering import MultiSourceFilterResult  # 返回稳定的过滤论文与统计。
from backend.app.models.paper import PaperRecord  # 使用已完成来源融合的论文记录。
from backend.app.models.query_intent import QueryIntent  # 使用查询规划输出的确定性约束。


class MultiSourcePaperFilter:
    """按 QueryIntent 的硬约束过滤融合论文，不承担相关性排序职责。"""

    def filter(self, papers: list[PaperRecord], query: QueryIntent) -> MultiSourceFilterResult:
        """依次应用年份、类型、venue、作者、机构、必须词和排除词规则。

        参数：
            papers：已完成身份融合并保留 RRF 的候选论文。
            query：包含可验证硬约束的完整查询意图。
        返回：
            MultiSourceFilterResult：保留论文、首个失败原因统计与版本族数量。
        """
        retained_papers: list[PaperRecord] = []  # 保存依次通过所有规则的论文。
        reason_counts: Counter[str] = Counter()  # 保存每条被移除论文的首个失败原因。
        for paper in papers:  # 保持融合服务给出的稳定相对顺序。
            failure_reason = self._first_failure_reason(paper, query)  # 仅取首个原因，使过滤统计总数可与移除数对齐。
            if failure_reason is not None:  # 任一硬约束失败时不进入后续排序。
                reason_counts[failure_reason] += 1  # 记录可安全展示的过滤原因数量。
                continue  # 继续处理下一篇论文。
            retained_papers.append(paper)  # 保留完全满足当前可验证硬约束的论文。
        result = MultiSourceFilterResult(  # 构造稳定过滤阶段输出。
            papers=retained_papers,  # 返回未改变相对顺序的保留论文。
            input_count=len(papers),  # 记录融合后进入过滤阶段的候选数量。
            filtered_count=len(papers) - len(retained_papers),  # 记录被确定性规则移除的数量。
            filter_reason_counts=dict(reason_counts),  # 转换为可序列化普通字典。
            work_family_count=len({paper.work_family_id for paper in retained_papers if paper.work_family_id}),  # 统计最终候选包含的唯一版本族。
        )
        logger.info("多源规则过滤完成：输入=%d，移除=%d，保留=%d，原因数=%d", result.input_count, result.filtered_count, len(result.papers), len(result.filter_reason_counts))  # 仅记录数量统计避免泄露查询或论文内容。
        return result  # 返回可交给 BGE-M3 粗排的确定性候选集合。

    @staticmethod
    def _first_failure_reason(paper: PaperRecord, query: QueryIntent) -> str | None:
        """返回论文首个未满足的硬约束原因；全部满足时返回空值。"""
        if not _matches_year_range(paper, query):  # 年份是明确且低成本的首要约束。
            return "year_range"  # 返回稳定的年份过滤原因。
        if not _matches_paper_type(paper, query):  # 论文类型为来源可验证的硬约束。
            return "paper_type"  # 返回稳定的类型过滤原因。
        if not _matches_venues(paper, query):  # venue 约束应在语义处理前确定性应用。
            return "venue"  # 返回稳定的 venue 过滤原因。
        if not _matches_authors(paper, query):  # 作者约束可由已融合作者字段直接验证。
            return "author"  # 返回稳定的作者过滤原因。
        if not _matches_institutions(paper, query):  # 机构约束仅基于来源提供的作者机构信息。
            return "institution"  # 返回稳定的机构过滤原因。
        if not _contains_all_required_terms(paper, query):  # 必须词全部命中后才允许进入排序。
            return "must_include"  # 返回稳定的硬关键词过滤原因。
        if _contains_excluded_term(paper, query):  # 命中任一排除词即应移除。
            return "exclude"  # 返回稳定的排除关键词过滤原因。
        return None  # 论文通过全部当前可验证规则。


def _normalize_text(value: str) -> str:
    """压缩空白并执行大小写折叠，供确定性文本比较使用。"""
    return " ".join(value.casefold().split())  # 消除大小写和连续空白的无语义差异。


def _normalized_terms(values: list[str]) -> list[str]:
    """过滤空白条件并返回可用于匹配的规范化查询词列表。"""
    return [normalized for value in values if (normalized := _normalize_text(value))]  # 保留输入顺序且忽略空白条件。


def _matches_year_range(paper: PaperRecord, query: QueryIntent) -> bool:
    """判断论文是否满足可选年份闭区间；指定时未知年份视为未通过。"""
    if query.year_range is None:  # 未指定年份范围时不施加过滤。
        return True  # 允许论文进入下一项规则。
    return paper.year is not None and query.year_range[0] <= paper.year <= query.year_range[1]  # 指定范围时要求来源提供可验证年份。


def _matches_paper_type(paper: PaperRecord, query: QueryIntent) -> bool:
    """判断论文是否属于任一指定类型；指定时未知类型视为未通过。"""
    return not query.paper_types or paper.paper_type in query.paper_types  # 无约束时保留全部，有约束时要求来源类型明确匹配。


def _matches_venues(paper: PaperRecord, query: QueryIntent) -> bool:
    """判断论文 venue 是否与任一指定名称相互包含，以兼容简称和全称。"""
    expected_venues = _normalized_terms(query.venues)  # 规范化有效 venue 约束。
    if not expected_venues:  # 未指定有效 venue 时无需过滤。
        return True  # 保留论文。
    normalized_venue = _normalize_text(paper.venue or "")  # 将缺失 venue 统一为不可匹配的空文本。
    return bool(normalized_venue) and any(expected in normalized_venue or normalized_venue in expected for expected in expected_venues)  # 兼容常见缩写与全称。


def _matches_authors(paper: PaperRecord, query: QueryIntent) -> bool:
    """判断论文作者是否匹配任一作者约束，支持全名或包含式匹配。"""
    expected_authors = _normalized_terms(query.authors)  # 规范化有效作者约束。
    if not expected_authors:  # 未指定作者时无需过滤。
        return True  # 保留论文。
    actual_authors = [_normalize_text(author.name) for author in paper.authors]  # 读取融合论文中的全部作者名称。
    return any(expected in actual or actual in expected for expected in expected_authors for actual in actual_authors if actual)  # 支持用户输入全名、来源缩写或常见显示差异。


def _matches_institutions(paper: PaperRecord, query: QueryIntent) -> bool:
    """判断作者来源机构是否匹配任一机构约束，缺失机构不能通过显式约束。"""
    expected_institutions = _normalized_terms(query.institutions)  # 规范化有效机构约束。
    if not expected_institutions:  # 未指定机构时无需过滤。
        return True  # 保留论文。
    actual_institutions = [_normalize_text(author.institution or "") for author in paper.authors]  # 读取来源提供的作者机构字段。
    return any(expected in actual or actual in expected for expected in expected_institutions for actual in actual_institutions if actual)  # 兼容机构简称和全称。


def _searchable_text(paper: PaperRecord) -> str:
    """组合标题、摘要和关键词，作为可解释的硬关键词匹配证据范围。"""
    return _normalize_text(" ".join([paper.title, paper.abstract, *paper.keywords]))  # 不使用外部网页内容或推断字段，避免虚构证据。


def _contains_all_required_terms(paper: PaperRecord, query: QueryIntent) -> bool:
    """判断标题、摘要和关键词是否包含全部必须词。"""
    required_terms = _normalized_terms(query.must_include)  # 规范化有效必须词。
    return not required_terms or all(term in _searchable_text(paper) for term in required_terms)  # 空约束直接通过，否则要求全部词命中。


def _contains_excluded_term(paper: PaperRecord, query: QueryIntent) -> bool:
    """判断标题、摘要和关键词是否命中任一排除词。"""
    excluded_terms = _normalized_terms(query.exclude)  # 规范化有效排除词。
    return any(term in _searchable_text(paper) for term in excluded_terms)  # 任一排除词命中即视为不满足约束。
