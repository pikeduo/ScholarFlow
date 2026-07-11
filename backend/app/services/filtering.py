"""在排序前按结构化查询执行确定性的本地论文过滤。"""

from backend.app.models.paper import Paper  # 使用统一论文模型读取可过滤字段。
from backend.app.models.query import QuerySchema  # 使用已校验的结构化查询约束。


def _normalize_text(value: str) -> str:
    """将文本转换为适合不区分大小写比较的紧凑形式。

    参数：
        value：待比较的原始文本。
    返回：
        str：已合并连续空白并统一大小写的文本。
    """
    return " ".join(value.casefold().split())  # 消除大小写和无语义空白差异。


def _matches_year_range(paper: Paper, query: QuerySchema) -> bool:
    """判断论文是否符合可选的发表年份闭区间。

    参数：
        paper：待判断的规范化论文。
        query：包含可选年份约束的结构化查询。
    返回：
        bool：未指定范围时为真；指定范围时要求论文年份存在且位于区间内。
    """
    if query.year_range is None:  # 未指定年份条件时不应过滤任何论文。
        return True  # 允许论文进入后续规则。
    if paper.year is None:  # 无法验证年份的论文不满足明确的时间约束。
        return False  # 避免将未知年份论文误判为符合范围。
    return query.year_range[0] <= paper.year <= query.year_range[1]  # 按闭区间判断发表年份。


def _matches_venue(paper: Paper, query: QuerySchema) -> bool:
    """判断论文的期刊或会议名称是否匹配任一指定 venue。

    参数：
        paper：待判断的规范化论文。
        query：包含可选 venue 条件的结构化查询。
    返回：
        bool：未指定有效 venue 时为真；指定时要求论文 venue 包含任一条件或反向包含。
    """
    expected_venues = [_normalize_text(venue) for venue in query.venue if venue.strip()]  # 过滤空条件并标准化匹配文本。
    if not expected_venues:  # 没有实际 venue 条件时保持全部论文。
        return True  # 允许论文进入后续规则。
    if paper.venue is None:  # 指定 venue 时不能将来源缺失的 venue 视为匹配。
        return False  # 防止无元数据论文绕过明确约束。
    normalized_venue = _normalize_text(paper.venue)  # 标准化数据源提供的会议或期刊名称。
    if not normalized_venue:  # 空白 venue 与来源未提供 venue 的业务含义相同。
        return False  # 防止空字符串意外匹配任何包含关系。
    return any(venue in normalized_venue or normalized_venue in venue for venue in expected_venues)  # 兼容全称和常见缩写的包含匹配。


def _contains_excluded_term(paper: Paper, query: QuerySchema) -> bool:
    """判断标题或摘要是否出现任一有效排除词。

    参数：
        paper：待判断的规范化论文。
        query：包含可选排除词的结构化查询。
    返回：
        bool：命中任一排除词时为真。
    """
    excluded_terms = [_normalize_text(term) for term in query.exclude if term.strip()]  # 过滤空排除词并统一比较形式。
    if not excluded_terms:  # 未指定有效排除词时无需检查文本内容。
        return False  # 表示论文未命中排除条件。
    searchable_text = _normalize_text(f"{paper.title} {paper.abstract}")  # 只使用可公开展示的标题和摘要进行本地匹配。
    return any(term in searchable_text for term in excluded_terms)  # 任一排除词命中即交由调用方过滤。


def _contains_all_required_terms(paper: Paper, query: QuerySchema) -> bool:
    """判断标题或摘要是否包含全部有效的必须包含词。

    参数：
        paper：待判断的规范化论文。
        query：包含可选必须包含词的结构化查询。
    返回：
        bool：未指定有效必须包含词时为真；否则要求每个词均命中标题或摘要。
    """
    required_terms = [_normalize_text(term) for term in query.must_include if term.strip()]  # 过滤空必须包含词并统一比较形式。
    if not required_terms:  # 未指定有效必须包含词时无需增加过滤条件。
        return True  # 表示论文满足空的必须包含条件。
    searchable_text = _normalize_text(f"{paper.title} {paper.abstract}")  # 只使用可公开展示的标题和摘要进行本地匹配。
    return all(term in searchable_text for term in required_terms)  # 只有全部必须包含词命中时才保留论文。


def filter_papers(papers: list[Paper], query: QuerySchema) -> list[Paper]:
    """按年份、venue、必须包含词和排除词保留进入排序阶段的论文。

    参数：
        papers：已完成规范化与去重、保持召回顺序的论文列表。
        query：已校验的结构化查询约束。
    返回：
        list[Paper]：保持原始相对顺序的本地规则过滤结果。
    """
    retained_papers: list[Paper] = []  # 保存依次通过全部本地规则的论文。
    for paper in papers:  # 按原始召回顺序处理，避免改变后续排序的稳定输入。
        if not _matches_year_range(paper, query):  # 首先应用发布时间约束。
            continue  # 排除超出范围或缺少必要年份的论文。
        if not _matches_venue(paper, query):  # 再应用期刊或会议约束。
            continue  # 排除未匹配指定 venue 的论文。
        if not _contains_all_required_terms(paper, query):  # 再要求标题和摘要包含全部必须包含词。
            continue  # 排除未覆盖任一必要关键词的论文。
        if _contains_excluded_term(paper, query):  # 最后应用标题和摘要中的排除词。
            continue  # 排除命中不希望主题的论文。
        retained_papers.append(paper)  # 保留通过全部规则的论文。
    return retained_papers  # 返回可进入语义粗排的稳定候选集。
