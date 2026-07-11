"""按稳定标识优先级去除多源论文检索结果中的重复记录。"""

import re  # 处理 arXiv 版本号后缀。

from backend.app.core.logging import logger  # 记录去重阶段的输入与输出统计。
from backend.app.models.paper import Paper  # 使用统一论文领域模型。


def _normalize_doi(doi: str) -> str:
    """将常见 DOI URL 或前缀转换为可比较的稳定标识。

    参数：
        doi：数据源返回的原始 DOI 文本。
    返回：
        str：去除展示前缀并统一小写的 DOI。
    """
    normalized_doi = doi.strip().casefold()  # 移除空白并消除 DOI 大小写差异。
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):  # 兼容常见 DOI 表示方式。
        if normalized_doi.startswith(prefix):  # 仅移除实际存在的展示前缀。
            normalized_doi = normalized_doi.removeprefix(prefix)  # 保留 DOI 的核心标识。
    return normalized_doi.rstrip("/")  # 忽略 URL 末尾无语义的斜杠。


def _normalize_arxiv_id(arxiv_id: str) -> str:
    """将 arXiv 标识转换为不区分版本的可比较形式。

    参数：
        arxiv_id：数据源返回的原始 arXiv 标识。
    返回：
        str：移除 arXiv 前缀与版本号后的标识。
    """
    normalized_id = arxiv_id.strip().casefold().removeprefix("arxiv:")  # 移除展示前缀并统一大小写。
    return re.sub(r"v\d+$", "", normalized_id)  # 将同一预印本的不同版本视为同一记录。


def _build_title_key(paper: Paper) -> str:
    """构造仅用于缺少稳定标识时的保守标题回退键。

    参数：
        paper：待构造回退键的论文。
    返回：
        str：由标题、年份和首位作者组成的标准化键。
    """
    normalized_title = " ".join(paper.title.casefold().split())  # 统一标题大小写与连续空白。
    first_author = paper.authors[0].name.casefold().strip() if paper.authors else ""  # 仅使用首位作者降低误合并概率。
    publication_year = str(paper.year) if paper.year is not None else ""  # 缺失年份时保留空位置。
    return "|".join((normalized_title, publication_year, first_author))  # 生成可哈希的回退比较键。


def deduplicate_papers(papers: list[Paper]) -> list[Paper]:
    """按 DOI、arXiv、同源平台 ID、标题回退键的顺序保留首次出现的论文。

    参数：
        papers：按数据源召回顺序排列的规范化论文列表。
    返回：
        list[Paper]：保持输入相对顺序的去重后论文列表。
    """
    retained_papers: list[Paper] = []  # 保存每个身份组首次出现的论文。
    seen_dois: set[str] = set()  # 记录已经保留的 DOI。
    seen_arxiv_ids: set[str] = set()  # 记录已经保留的 arXiv 标识。
    seen_source_ids: set[str] = set()  # 记录同一来源内已经保留的平台标识。
    seen_title_keys: set[str] = set()  # 记录缺少稳定标识论文的标题回退键。
    duplicate_count = 0  # 统计被过滤的重复论文数量。

    for paper in papers:  # 按输入顺序处理，确保优先保留靠前的数据源结果。
        if paper.doi:  # DOI 是跨来源最可靠的论文标识。
            doi_key = _normalize_doi(paper.doi)  # 规范化 DOI 以识别 URL 和大小写差异。
            if doi_key in seen_dois:  # 相同 DOI 表示同一论文。
                duplicate_count += 1  # 累加 DOI 重复计数。
                continue  # 跳过后续重复记录。
            seen_dois.add(doi_key)  # 标记当前 DOI 已保留。
            retained_papers.append(paper)  # 保留首次出现的 DOI 记录。
            continue  # DOI 记录不使用低优先级字段再次判断。

        if paper.arxiv_id:  # 无 DOI 时使用 arXiv 标识匹配预印本。
            arxiv_key = _normalize_arxiv_id(paper.arxiv_id)  # 忽略 arXiv 展示前缀与版本号。
            if arxiv_key in seen_arxiv_ids:  # 相同 arXiv 标识表示同一预印本。
                duplicate_count += 1  # 累加 arXiv 重复计数。
                continue  # 跳过后续预印本记录。
            seen_arxiv_ids.add(arxiv_key)  # 标记当前 arXiv 标识已保留。
            retained_papers.append(paper)  # 保留首次出现的预印本记录。
            continue  # arXiv 记录不使用低优先级字段再次判断。

        source_key = f"{paper.source}:{paper.paper_id.strip()}"  # 构造同源平台内的稳定标识。
        if source_key in seen_source_ids:  # 同一来源的相同平台 ID 必然指向同一记录。
            duplicate_count += 1  # 累加平台 ID 重复计数。
            continue  # 跳过同源重复记录。

        title_key = _build_title_key(paper)  # 为缺少 DOI 和 arXiv 的跨源记录构造回退键。
        if title_key in seen_title_keys:  # 相同标题、年份和首位作者视为待合并重复项。
            duplicate_count += 1  # 累加标题回退重复计数。
            continue  # 跳过回退规则识别的重复记录。

        seen_source_ids.add(source_key)  # 标记平台标识已保留。
        seen_title_keys.add(title_key)  # 标记标题回退键已保留。
        retained_papers.append(paper)  # 保留未命中任何重复条件的论文。

    logger.info("论文去重完成：输入=%d，保留=%d，重复=%d", len(papers), len(retained_papers), duplicate_count)  # 记录去重阶段统计。
    return retained_papers  # 返回保持输入顺序的去重结果。
