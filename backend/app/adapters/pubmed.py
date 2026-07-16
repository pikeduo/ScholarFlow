"""封装 PubMed E-utilities 检索、来源级节流与统一论文映射。"""

import re  # 从 PubMed 非结构化日期文本中提取可用年份。
import xml.etree.ElementTree as ElementTree  # 使用标准库解析 EFetch 返回的 PubMed XML。
import httpx  # 提供异步 HTTP 客户端和可注入测试传输层。

from backend.app.adapters.base import AcademicSearchAdapter  # 实现统一学术来源适配器协议。
from backend.app.adapters.academic_api import AcademicApiNetworkError, AcademicApiRequestExecutor  # 复用统一的幂等请求重试、RPS 与冷却边界。
from backend.app.core.config import Settings, settings  # 读取 PubMed 端点、超时和来源限流配置。
from backend.app.core.logging import logger  # 记录不包含完整查询的来源调用统计与错误。
from backend.app.models.paper import PaperAuthor, PaperRecord, PaperSourceRecord  # 构造保留 PubMed 溯源信息的统一论文记录。
from backend.app.models.query_intent import QueryIntent  # 接收查询规划节点输出的统一检索意图。
from backend.app.repositories.source_rate_limiter import SourceCooldownError, SourceRateLimiter  # 将共享冷却状态转换为来源领域异常。

YEAR_PATTERN = re.compile(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)")  # 匹配 PaperRecord 支持范围内的四位出版年份。


class PubMedMappingError(ValueError):
    """表示 PubMed EFetch 条目缺少生成统一论文记录所必需的数据。"""


class PubMedClientError(RuntimeError):
    """表示 PubMed HTTP 调用、响应结构或 XML 解析不可用。"""


def build_pubmed_esearch_params(query: QueryIntent) -> dict[str, str | int]:
    """将 QueryIntent 转换为 PubMed ESearch 的单页参数。

    参数：
        query：已由查询规划节点校验的统一检索意图。
    返回：
        dict[str, str | int]：不含联系信息的 ESearch 请求参数。
    """
    search_terms: list[str] = []  # 按确定顺序收集结构化查询中的可用检索词。
    for terms in (query.research_topics, query.methods, query.tasks, query.datasets, query.must_include):  # 合并主题、方法、任务、数据集和硬约束。
        search_terms.extend(_normalize_search_term(term) for term in terms if _normalize_search_term(term))  # 规范化术语并忽略空白值。
    search_text = " AND ".join(f"({term})" for term in search_terms) or _normalize_search_term(query.normalized_query)  # 缺少拆分术语时回退到完整英文检索式。
    if query.year_range is not None:  # PubMed 使用出版日期字段表达用户明确给定的年份约束。
        search_text = f"({search_text}) AND ({query.year_range[0]}:{query.year_range[1]}[dp])"  # 使用闭区间避免改变已有 QueryIntent 元组契约。
    return {
        "db": "pubmed",  # 固定查询 PubMed 数据库。
        "term": search_text,  # 将结构化意图映射为 PubMed 术语表达式。
        "retmode": "json",  # 请求便于稳定读取 PMID 列表的 JSON 响应。
        "retmax": query.source_recall_count or query.target_paper_count,  # 使用来源召回规模并兼容旧查询契约。
        "sort": "relevance",  # 优先获得 PubMed 的相关性排序候选。
    }


class PubMedClient(AcademicSearchAdapter):
    """实现 PubMed ESearch、EFetch、XML 校验与来源级限流。

    参数：
        settings_override：测试或多环境场景下可替换的配置对象。
        transport：可选 HTTP 传输层，仅用于离线单元测试或定制网络策略。
    """

    source = "pubmed"  # 声明当前客户端实现的统一来源名称。

    def __init__(
        self,
        settings_override: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        source_rate_limiter: SourceRateLimiter | None = None,
        request_executor: AcademicApiRequestExecutor | None = None,
    ) -> None:
        """保存配置、测试传输层和来源级节流状态。"""
        self._settings = settings_override or settings  # 默认复用经环境变量校验的全局配置。
        self._transport = transport  # 保留可由测试替换的 HTTP 传输层。
        self._request_executor = request_executor or AcademicApiRequestExecutor("pubmed", self._settings, self._settings.pubmed_requests_per_second, source_rate_limiter=source_rate_limiter)  # ESearch 与 EFetch 共用同一来源窗口和冷却状态。

    async def search(self, query: QueryIntent) -> list[PaperRecord]:
        """检索 PubMed 并返回保留 PMID、DOI 与来源排名的统一论文记录。"""
        esearch_params = build_pubmed_esearch_params(query)  # 先构造不含联系信息的可测试检索参数。
        request_params = self._with_common_params(esearch_params)  # 再在 HTTP 边界添加 NCBI tool 和可选邮箱。
        async with httpx.AsyncClient(
            base_url=self._settings.pubmed_api_base_url.rstrip("/") + "/",  # 确保 E-utilities 子路径可稳定拼接。
            timeout=self._settings.pubmed_timeout_seconds,  # 限制单个 E-utilities 请求等待时间。
            transport=self._transport,  # 离线测试使用 MockTransport，生产环境使用默认网络传输。
        ) as client:
            search_payload = await self._get_json(client, "esearch.fcgi", request_params)  # 第一步仅获取按相关性排序的 PMID。
            pmids = _extract_pmids(search_payload)  # 校验并提取可供 EFetch 使用的唯一 PMID 列表。
            if not pmids:  # PubMed 正常空结果不应被当作来源错误。
                return []  # 无候选时避免无意义地调用第二个端点。
            fetch_payload = await self._get_text(  # 第二步批量获取可展示且可去重的论文元数据。
                client,
                "efetch.fcgi",
                self._with_common_params({"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}),
            )
        try:  # XML 与单条映射异常需要净化为统一来源错误。
            root = ElementTree.fromstring(fetch_payload)  # 解析 PubMedArticleSet XML 根节点。
        except ElementTree.ParseError as error:  # 非 XML 或损坏 XML 不能进入后续字段映射。
            raise PubMedClientError("PubMed EFetch 返回了无法解析的 XML") from error  # 不泄露原始响应正文。
        if root.tag == "ERROR" or root.find("ERROR") is not None:  # E-utilities 可能以 XML 错误节点返回逻辑错误。
            raise PubMedClientError("PubMed EFetch 返回了来源错误")  # 统一净化来源端的详细报错。
        rank_by_pmid = {pmid: index for index, pmid in enumerate(pmids, start=1)}  # 保留 ESearch 的来源原始排名供 RRF 使用。
        papers: list[PaperRecord] = []  # 累积成功映射的候选，单条异常不阻断其他正常条目。
        for article in root.findall("./PubmedArticle"):  # 只处理 PubMedArticleSet 中的标准论文节点。
            try:  # 缺字段条目需要安全跳过并保留其他结果。
                paper = map_pubmed_article(article, rank_by_pmid)  # 映射包含 PMID、DOI、摘要和期刊信息的统一记录。
            except PubMedMappingError:  # 来源数据偶发缺失不应使整次 PubMed 调用失败。
                logger.warning("PubMed 论文条目字段不完整，已跳过")  # 不记录标题、摘要或原始响应内容。
                continue  # 继续处理同一批次中的其他论文。
            papers.append(paper)  # 保留已完成统一映射的论文记录。
        logger.info("PubMed 搜索完成：映射论文数=%d", len(papers))  # 仅记录可观测数量，不记录查询正文。
        return papers  # 交给协调器执行去重、融合和分层排序。

    async def _get_json(self, client: httpx.AsyncClient, path: str, params: dict[str, str | int]) -> dict[str, object]:
        """按来源级节流请求并校验 PubMed JSON 响应。"""
        response = await self._request(client, path, params)  # 复用受控 HTTP 错误处理与节流边界。
        try:  # httpx 会在 JSON 无效时抛出 ValueError。
            payload = response.json()  # 将 ESearch 响应解析为 Python 对象。
        except ValueError as error:  # 非 JSON 响应不能进入结构化解析。
            raise PubMedClientError("PubMed ESearch 返回了无效 JSON") from error  # 不泄露上游响应正文。
        if not isinstance(payload, dict):  # 仅接受对象形式的 ESearch 成功响应。
            raise PubMedClientError("PubMed ESearch 返回结构无效")  # 避免不可信响应结构触发类型错误。
        return payload  # 返回已通过最小结构检查的 ESearch 数据。

    async def _get_text(self, client: httpx.AsyncClient, path: str, params: dict[str, str]) -> str:
        """按来源级节流请求并返回 PubMed XML 文本。"""
        response = await self._request(client, path, params)  # 复用相同的 HTTP 错误和节流边界。
        return response.text  # XML 由调用方使用标准库独立解析。

    async def _request(self, client: httpx.AsyncClient, path: str, params: dict[str, str | int]) -> httpx.Response:
        """在本地来源级限流后请求单个 E-utilities 端点。"""
        try:  # 网络错误和 HTTP 状态错误均需转换为安全的领域异常。
            response = await self._request_executor.execute(lambda: client.get(path, params=params))  # 每次重试均重新经过来源 RPS 与 Redis 窗口。
            response.raise_for_status()  # 将 4xx/5xx 响应转入统一异常边界。
        except httpx.HTTPStatusError as error:  # 状态错误仅保留状态码，不暴露上游正文。
            raise PubMedClientError(f"PubMed 请求失败：HTTP {error.response.status_code}") from error  # 向协调器提供可审计错误类别。
        except httpx.RequestError as error:  # DNS、连接和超时错误均不携带可展示的内部细节。
            raise PubMedClientError("PubMed 请求失败：网络或超时错误") from error  # 使协调器能够按单来源安全降级。
        except AcademicApiNetworkError:  # 统一执行器耗尽临时网络重试后保持既有领域错误契约。
            raise PubMedClientError("PubMed 请求失败：网络或超时错误") from None  # 不泄露底层传输细节。
        except SourceCooldownError:  # 冷却期内新请求不得继续访问任意 E-utilities 端点。
            raise PubMedClientError("PubMed 请求受限，当前处于冷却期") from None  # 让多源协调器继续使用其他来源。
        return response  # 返回已确认成功的 HTTP 响应。

    def _with_common_params(self, params: dict[str, str | int]) -> dict[str, str | int]:
        """为 PubMed 请求添加应用标识与可选联系邮箱。"""
        result = {**params, "tool": self._settings.pubmed_tool}  # 始终发送来源建议的应用标识。
        if self._settings.pubmed_email:  # 联系邮箱未配置时不发送空参数。
            result["email"] = self._settings.pubmed_email  # 仅在 HTTP 边界携带用户配置的联系信息。
        return result  # 返回不修改调用方参数字典的新对象。


def map_pubmed_article(article: ElementTree.Element, rank_by_pmid: dict[str, int]) -> PaperRecord:
    """将一个 PubmedArticle XML 节点映射为统一论文记录。

    参数：
        article：EFetch 返回的单个 PubMed 论文 XML 节点。
        rank_by_pmid：由 ESearch 返回顺序建立的 PMID 与来源排名映射。
    返回：
        PaperRecord：带 PubMed 溯源、PMID、DOI 与基础元数据的统一论文记录。
    异常：
        PubMedMappingError：PMID 或标题缺失时抛出。
    """
    pmid = _element_text(article.find("./MedlineCitation/PMID"))  # PubMed PMID 是该来源的稳定论文标识。
    title = _join_element_text(article.find("./MedlineCitation/Article/ArticleTitle"))  # 标题可能包含嵌套的斜体或上下标节点。
    if pmid is None or title is None:  # 统一模型要求稳定标识和可展示标题。
        raise PubMedMappingError("PubMed 条目缺少 PMID 或标题")  # 避免生成无法去重或展示的不完整记录。
    abstract = _extract_abstract(article)  # 摘要可为空，但应拼接带标签的多个 AbstractText 片段。
    authors = _extract_authors(article)  # PubMed 作者与集体作者均应保留为统一作者对象。
    doi = _extract_doi(article)  # DOI 缺失时保持空值，后续可继续按 PMID 去重。
    publication_types = _extract_publication_types(article)  # 使用 PubMed 原始出版类型做保守映射。
    return PaperRecord(
        paper_id=f"pubmed:{pmid}",  # 使用来源命名空间避免与其他平台 ID 冲突。
        title=title,  # 保留已去除 XML 标记的完整论文标题。
        abstract=abstract,  # 缺失摘要时返回空字符串，符合统一领域模型约定。
        authors=authors,  # 写入已规范化的作者列表。
        year=_extract_year(article),  # 无法可靠读取年份时保持未知而不虚构日期。
        venue=_join_element_text(article.find("./MedlineCitation/Article/Journal/Title")),  # 保留期刊全称作为出版载体。
        doi=doi,  # 保留 DOI 供跨来源第一优先级去重。
        pmid=pmid,  # 保留 PMID 供医学文献身份关联。
        source="pubmed",  # 标记当前规范化记录的来源。
        paper_type=_map_paper_type(publication_types),  # 仅在类型可可靠识别时填充统一枚举。
        source_records=[PaperSourceRecord(source="pubmed", external_id=pmid, raw_rank=rank_by_pmid.get(pmid))],  # 保存 ESearch 原始排名供 RRF 融合使用。
    )


def _extract_pmids(payload: dict[str, object]) -> list[str]:
    """从 ESearch JSON 中提取保序且去重的 PMID 列表。"""
    result = payload.get("esearchresult")  # ESearch 成功响应将结果包装在固定字段中。
    if not isinstance(result, dict):  # 缺失结果对象代表上游结构已变化或来源错误。
        raise PubMedClientError("PubMed ESearch 返回结构无效")  # 不让下游依赖不可信的字典层级。
    id_list = result.get("idlist")  # PMID 列表以字符串数组形式提供。
    if not isinstance(id_list, list):  # 空结果也应返回空数组而非其他类型。
        raise PubMedClientError("PubMed ESearch 未返回 PMID 列表")  # 将结构异常与正常空结果区分。
    pmids: list[str] = []  # 保留来源排序的可用 PMID 列表。
    seen: set[str] = set()  # 防御上游异常重复 ID，避免重复映射同一论文。
    for value in id_list:  # 按 ESearch 原始相关性顺序遍历。
        pmid = str(value).strip()  # 统一将 JSON 值规范为去除空白的字符串。
        if pmid and pmid not in seen:  # 忽略空值并保持首次出现的来源排名。
            seen.add(pmid)  # 标记 PMID 已保留。
            pmids.append(pmid)  # 记录可供 EFetch 批量请求的 PMID。
    return pmids  # 无命中时返回稳定空列表。


def _extract_abstract(article: ElementTree.Element) -> str:
    """拼接 PubMed 的多段摘要并保留可读的段落标签。"""
    fragments: list[str] = []  # 收集每个 AbstractText 的可展示文本片段。
    for abstract_text in article.findall("./MedlineCitation/Article/Abstract/AbstractText"):  # PubMed 摘要可能按背景、方法等多段返回。
        text = _join_element_text(abstract_text)  # 合并包含嵌套格式节点的全部文本。
        if text is None:  # 空段落不应留下多余分隔符。
            continue  # 继续读取下一段摘要。
        label = _normalize_text(abstract_text.get("Label"))  # 可选标签用于保留结构化摘要语义。
        fragments.append(f"{label}: {text}" if label else text)  # 仅在存在标签时添加简洁前缀。
    return "\n".join(fragments)  # 以换行分隔多段，便于前端展示和排序文本编码。


def _extract_authors(article: ElementTree.Element) -> list[PaperAuthor]:
    """提取个人作者和集体作者为统一作者列表。"""
    authors: list[PaperAuthor] = []  # 累积保持 PubMed 原始顺序的作者对象。
    for author in article.findall("./MedlineCitation/Article/AuthorList/Author"):  # PubMed 以独立 XML 节点表达作者。
        collective_name = _element_text(author.find("CollectiveName"))  # 机构署名或协作组使用 CollectiveName 字段。
        if collective_name is not None:  # 集体作者无需再拼接个人姓名字段。
            authors.append(PaperAuthor(name=collective_name))  # 保留可展示的协作组名称。
            continue  # 继续读取下一位作者。
        last_name = _element_text(author.find("LastName"))  # 个人作者通常提供姓氏。
        fore_name = _element_text(author.find("ForeName"))  # 个人作者可能提供完整名字。
        initials = _element_text(author.find("Initials"))  # 缺少完整名字时使用缩写作为补充。
        name = " ".join(part for part in (fore_name, last_name) if part) or " ".join(part for part in (initials, last_name) if part)  # 优先形成自然显示顺序的姓名。
        if name:  # 统一作者模型不接受空名称。
            authors.append(PaperAuthor(name=name))  # 写入已规范化的个人作者。
    return authors  # 缺少作者列表时返回稳定空列表。


def _extract_doi(article: ElementTree.Element) -> str | None:
    """从 PubMedData 的文章标识列表中读取 DOI。"""
    for identifier in article.findall("./PubmedData/ArticleIdList/ArticleId"):  # PubMed 在此节点中统一提供 DOI、PMC 等标识。
        if (identifier.get("IdType") or "").casefold() == "doi":  # 仅接收可用于跨来源首要去重的 DOI。
            return _join_element_text(identifier)  # DOI 可能含额外空白，需要统一规范化。
    return None  # 来源未提供 DOI 时保持未知。


def _extract_publication_types(article: ElementTree.Element) -> list[str]:
    """读取 PubMed 出版类型列表，供保守的统一类型映射使用。"""
    return [text for item in article.findall("./MedlineCitation/Article/PublicationTypeList/PublicationType") if (text := _join_element_text(item)) is not None]  # 过滤空节点并保留来源顺序。


def _extract_year(article: ElementTree.Element) -> int | None:
    """按可靠性优先级从 PubMed XML 中提取出版年份。"""
    year_paths = (  # 优先读取结构化发表日期，再回退期刊出版日期。
        "./MedlineCitation/Article/ArticleDate/Year",  # ArticleDate 最贴近实际发表日期。
        "./MedlineCitation/Article/Journal/JournalIssue/PubDate/Year",  # 期刊卷期通常提供明确年份。
        "./MedlineCitation/DateCompleted/Year",  # 索引完成日期仅在前两者缺失时作为保守回退。
    )
    for path in year_paths:  # 依次读取可直接解析的年份节点。
        text = _element_text(article.find(path))  # 获取去除空白后的节点文本。
        if text and text.isdigit() and 1800 <= int(text) <= 2100:  # 与统一领域模型的年份范围保持一致。
            return int(text)  # 返回首个可信的结构化年份。
    medline_date = _element_text(article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate"))  # 部分旧记录只提供自由文本日期。
    match = YEAR_PATTERN.search(medline_date or "")  # 从自由文本中保守提取一个四位年份。
    return int(match.group(0)) if match else None  # 无法可靠提取时保持未知。


def _map_paper_type(publication_types: list[str]) -> str | None:
    """将 PubMed 出版类型保守映射为统一论文类型。"""
    normalized_types = {publication_type.casefold() for publication_type in publication_types}  # 忽略大小写后便于稳定匹配。
    if any("review" in publication_type for publication_type in normalized_types):  # Review、Systematic Review 等均映射为综述。
        return "review"  # 返回统一综述类型。
    if any("conference" in publication_type for publication_type in normalized_types):  # 仅明确会议文献才映射为会议类型。
        return "conference"  # 返回统一会议论文类型。
    if "journal article" in normalized_types:  # PubMed 最常见的明确研究文章类型。
        return "article"  # 返回统一期刊文章类型。
    return None  # 其他类型不强行映射为不准确的枚举值。


def _join_element_text(element: ElementTree.Element | None) -> str | None:
    """合并一个 XML 元素及其嵌套子元素中的可见文本。"""
    return _normalize_text("".join(element.itertext())) if element is not None else None  # 保留标题与摘要中的嵌套格式文本。


def _element_text(element: ElementTree.Element | None) -> str | None:
    """读取不含嵌套格式的 XML 元素文本并规范化空白。"""
    return _normalize_text(element.text) if element is not None else None  # 元素缺失时稳定返回空值。


def _normalize_search_term(value: str) -> str:
    """压缩检索词空白，避免向 PubMed 传递空表达式。"""
    return " ".join(value.split())  # 保留用户英文检索词的内容与顺序。


def _normalize_text(value: str | None) -> str | None:
    """压缩 XML 文本中的多余空白并将空结果统一为 None。"""
    normalized = " ".join(value.split()) if value else ""  # 处理换行、制表符与空节点。
    return normalized or None  # 空文本不应违反 PaperRecord 的非空字段约束。
