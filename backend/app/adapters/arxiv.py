"""封装 arXiv Atom 搜索、来源级节流与统一论文映射。"""

import asyncio  # 串行控制 arXiv 建议的连续请求间隔。
import re  # 规范化 arXiv 标识中的可选版本号。
import xml.etree.ElementTree as ElementTree  # 使用标准库解析 arXiv 返回的 Atom XML。
from urllib.parse import unquote, urlparse  # 从 arXiv 抽象页 URL 提取稳定论文标识。

import httpx  # 提供异步 HTTP 客户端和可注入测试传输层。

from backend.app.adapters.base import AcademicSearchAdapter  # 实现 LangGraph 可替换的统一适配器协议。
from backend.app.core.config import Settings, settings  # 读取 arXiv 地址、超时和来源级限流配置。
from backend.app.core.logging import logger  # 记录不含完整查询的来源调用统计与错误。
from backend.app.models.paper import PaperAuthor, PaperRecord, PaperSourceRecord  # 构造保留来源溯源信息的统一论文记录。
from backend.app.models.query_intent import QueryIntent  # 接收查询规划节点输出的统一意图。


ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"  # 声明 arXiv Atom 1.0 元素命名空间。
ARXIV_NAMESPACE = "http://arxiv.org/schemas/atom"  # 声明 arXiv 扩展元数据命名空间。
ARXIV_IDENTIFIER_VERSION_PATTERN = re.compile(r"v\d+$")  # 匹配现代与旧式 arXiv 标识末尾的版本号。


class ArxivMappingError(ValueError):
    """表示 arXiv Atom 条目缺少生成统一论文所必需的数据。"""


class ArxivClientError(RuntimeError):
    """表示 arXiv HTTP、Atom 解析或来源错误响应不可用。"""


def build_arxiv_search_params(query: QueryIntent) -> dict[str, str | int]:
    """将 QueryIntent 转换为 arXiv Atom 单页搜索参数。

    参数：
        query：已由查询规划节点校验的统一检索意图。
    返回：
        dict[str, str | int]：不含密钥、可直接用于 `/query` 端点的请求参数。
    """
    search_terms: list[str] = []  # 按确定顺序收集可由 arXiv 全字段搜索表达的词语。
    for terms in (query.research_topics, query.methods, query.tasks, query.datasets, query.must_include):  # 合并主题、方法、任务、数据集与硬约束。
        search_terms.extend(_normalize_search_term(term) for term in terms if _normalize_search_term(term))  # 规范化词语并跳过空白项。
    if not search_terms:  # QueryIntent 可以只携带未拆分的规范化查询。
        search_terms.append(_normalize_search_term(query.normalized_query))  # 使用必填规范化查询避免构造无约束检索。
    clauses = [f'all:"{term}"' for term in search_terms]  # 将每项强制限定为全字段文本，不接受用户注入的 arXiv 语法。
    if query.year_range:  # arXiv 只提供投稿日期过滤，因此以此作为发表年份的近似前置过滤。
        start_year, end_year = query.year_range  # 解构已由 QueryIntent 校验的闭区间年份。
        clauses.append(f"submittedDate:[{start_year}01010000 TO {end_year}12312359]")  # 使用官方要求的 GMT 分钟时间范围格式。
    return {  # 返回官方 Query API 使用的单页参数。
        "search_query": " AND ".join(clauses),  # 使用 AND 保持结构化条件的确定性语义。
        "start": 0,  # 首版仅请求每次搜索的第一页。
        "max_results": query.target_paper_count,  # 将目标结果规模映射为来源单页返回上限。
        "sortBy": "relevance",  # 保留来源默认的相关性排序供后续融合使用。
        "sortOrder": "descending",  # 使用官方支持的降序排序取值。
    }


class ArxivClient(AcademicSearchAdapter):
    """实现 arXiv 单页论文搜索、Atom 解析与三秒来源级节流。

    参数：
        settings_override：测试或多环境场景下可替换的配置对象。
        transport：可选 HTTP 传输层，仅用于无网络单元测试或定制网络策略。
    """

    source = "arxiv"  # 声明当前客户端实现的统一来源名称。

    def __init__(
        self,
        settings_override: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """保存配置、测试传输层与来源级节流状态。"""
        self._settings = settings_override or settings  # 默认复用经环境变量校验的全局配置。
        self._transport = transport  # 保留可由测试替换的 HTTP 传输层。
        self._rate_limit_lock = asyncio.Lock()  # 串行化同一客户端的请求起始时间。
        self._next_request_at = 0.0  # 保存下一次允许发起请求的事件循环时间。

    async def search(self, query: QueryIntent) -> list[PaperRecord]:
        """搜索 arXiv 并返回保留来源排名的统一论文记录。

        参数：
            query：包含检索词、年份和目标数量的完整查询意图。
        返回：
            list[PaperRecord]：已映射且保留 arXiv 来源溯源信息的论文列表。
        异常：
            ArxivClientError：HTTP、Atom 解析或来源错误响应时抛出。
        """
        await self._wait_for_rate_limit()  # 在请求前遵守配置化的 arXiv 最小间隔。
        params = build_arxiv_search_params(query)  # 构造不含用户密钥的可测试请求参数。
        try:  # 将 HTTP 层异常转换为不泄露响应正文的领域错误。
            async with httpx.AsyncClient(  # 为单次请求创建可自动关闭的异步客户端。
                base_url=self._settings.arxiv_api_base_url,  # 使用集中配置的 arXiv API 地址。
                timeout=self._settings.arxiv_timeout_seconds,  # 使用集中配置的请求超时。
                transport=self._transport,  # 在测试时使用本地 MockTransport。
                headers={"User-Agent": "ScholarWeave/0.1 (academic-search)"},  # 标识客户端用途但不发送用户数据或密钥。
            ) as client:
                response = await client.get("/query", params=params)  # 请求 arXiv Atom Query API。
                response.raise_for_status()  # 将非成功 HTTP 状态转换为可统一处理的异常。
                entries = parse_arxiv_atom_feed(response.text)  # 解析 Atom XML 并提前识别来源内错误条目。
        except httpx.HTTPStatusError as error:  # 单独记录不含响应正文的 HTTP 状态码。
            logger.error("arXiv 请求失败，状态码=%d", error.response.status_code)  # 输出安全且可观测的来源错误。
            raise ArxivClientError(f"arXiv 请求失败（HTTP {error.response.status_code}）") from None  # 隐藏底层请求上下文。
        except httpx.RequestError as error:  # 捕获连接、超时和传输失败。
            logger.error("arXiv 网络请求失败，错误类型=%s", type(error).__name__)  # 仅记录安全的异常类型。
            raise ArxivClientError("arXiv 网络请求失败") from None  # 返回稳定的领域错误。
        except ArxivClientError:  # 保留已净化的来源内错误条目说明。
            raise  # 不再包装已可安全展示给调用方的领域异常。
        except ElementTree.ParseError:  # 捕获响应非 Atom XML 或 XML 格式损坏。
            logger.error("arXiv 响应不是有效 Atom XML")  # 不记录可能过大的原始响应正文。
            raise ArxivClientError("arXiv 响应格式无效") from None  # 返回不泄露解析器内部细节的稳定错误。

        papers: list[PaperRecord] = []  # 保存成功映射的统一多源论文记录。
        skipped_count = 0  # 统计字段不完整而无法映射的单条 Atom 条目数量。
        for raw_rank, entry in enumerate(entries, start=1):  # 保留来源返回顺序作为 RRF 所需的原始排名。
            try:  # 单条映射失败不应丢弃整页可用结果。
                papers.append(map_arxiv_entry(entry, raw_rank=raw_rank))  # 映射并保留来源原始排名。
            except ArxivMappingError:  # 仅跳过缺少必要标识或标题的条目。
                skipped_count += 1  # 累加映射失败统计。
        logger.info("arXiv 检索完成：原始结果=%d，映射成功=%d，跳过=%d", len(entries), len(papers), skipped_count)  # 记录不含完整查询的阶段统计。
        return papers  # 返回可直接进入多源融合的统一论文记录。

    async def _wait_for_rate_limit(self) -> None:
        """按配置化 RPS 串行等待下一次允许发起 arXiv 请求的时间。"""
        async with self._rate_limit_lock:  # 防止同一客户端并发请求绕过来源级限额。
            loop = asyncio.get_running_loop()  # 使用事件循环单调时间避免系统时钟调整影响间隔。
            now = loop.time()  # 读取当前单调时间。
            wait_seconds = max(0.0, self._next_request_at - now)  # 计算距离允许请求还需等待的时间。
            if wait_seconds > 0:  # 仅在连续调用过快时等待。
                logger.info("arXiv 限流等待：秒数=%.3f", wait_seconds)  # 记录来源级等待统计，不记录查询内容。
                await asyncio.sleep(wait_seconds)  # 让出事件循环并遵守请求最小间隔。
            self._next_request_at = loop.time() + (1.0 / self._settings.arxiv_requests_per_second)  # 预约下一次允许请求的时间。


def parse_arxiv_atom_feed(xml_text: str) -> list[ElementTree.Element]:
    """解析 arXiv Atom XML，并将来源内错误条目转换为稳定异常。

    参数：
        xml_text：HTTP 响应解码后的 Atom XML 文本。
    返回：
        list[ElementTree.Element]：按来源顺序返回的论文 Atom 条目。
    异常：
        ArxivClientError：Atom 源内返回错误条目时抛出。
        ElementTree.ParseError：XML 不合法时抛出，调用方负责转换错误边界。
    """
    root = ElementTree.fromstring(xml_text)  # 使用标准库解析已由 HTTP 客户端解码的 Atom 文本。
    entries = root.findall(_atom_tag("entry"))  # 读取所有标准 Atom 论文条目。
    for entry in entries:  # arXiv 将部分查询错误以 HTTP 200 的单个 Error 条目返回。
        if _element_text(entry, "title") == "Error":  # 识别官方错误条目的固定标题。
            detail = _element_text(entry, "summary") or "arXiv 返回了未知查询错误"  # 提取安全的来源错误摘要。
            raise ArxivClientError(f"arXiv 查询错误：{detail}")  # 阻止错误条目被误映射为论文。
    return entries  # 返回已排除来源内错误的论文条目列表。


def map_arxiv_entry(entry: ElementTree.Element, raw_rank: int | None = None) -> PaperRecord:
    """将一条 arXiv Atom 条目映射为可溯源的 PaperRecord。

    参数：
        entry：由 Atom XML 解析器返回的单篇论文条目。
        raw_rank：该论文在当前来源搜索结果中的一开始排名。
    返回：
        PaperRecord：可进入多源融合和后续排序的规范化论文记录。
    异常：
        ArxivMappingError：缺少有效 arXiv 标识或标题时抛出。
    """
    abstract_url = _required_element_text(entry, "id")  # 读取可解析为 arXiv 稳定标识的论文抽象页 URL。
    arxiv_id = _extract_arxiv_id(abstract_url)  # 去除 URL 前缀和版本号得到跨源去重所需标识。
    published_text = _element_text(entry, "published")  # 读取首版投稿时间以映射可展示年份。
    return PaperRecord(  # 构造并交由 Pydantic 二次校验的多源论文记录。
        paper_id=f"arxiv:{arxiv_id}",  # 使用带来源前缀的稳定标识避免跨来源主键冲突。
        title=_required_element_text(entry, "title"),  # 使用 Atom 标准论文标题字段。
        abstract=_element_text(entry, "summary") or "",  # 缺失摘要时保留空字符串以支持部分元数据。
        authors=_extract_authors(entry),  # 规范化作者名称与可选机构信息。
        year=_extract_year(published_text),  # 从首版投稿时间提取可展示年份。
        venue=_arxiv_element_text(entry, "journal_ref"),  # 保留作者提供的可选期刊参考信息。
        doi=_arxiv_element_text(entry, "doi"),  # 映射 arXiv 扩展中的可选 DOI。
        arxiv_id=arxiv_id,  # 显式保留用于跨来源去重的 arXiv 标识。
        citation_count=0,  # arXiv Atom API 不提供引用次数，不能虚构质量信号。
        references=[],  # arXiv Atom API 不提供真实引用关系，保持为空列表。
        source="arxiv",  # 标记当前统一记录的主来源。
        keywords=_extract_categories(entry),  # 将来源分类映射为可展示和后续排序的关键词。
        paper_type="preprint",  # arXiv 记录默认表示预印本来源。
        is_open_access=True,  # arXiv 论文条目可通过公开抽象页或 PDF 访问。
        open_access_url=_extract_open_access_url(entry, abstract_url),  # 优先保留来源返回的公开 PDF 链接。
        source_records=[PaperSourceRecord(source="arxiv", external_id=arxiv_id, raw_rank=raw_rank)],  # 写入来源与原始排名供融合解释使用。
    )


def _normalize_search_term(value: str) -> str:
    """规范化单个 QueryIntent 词语以安全嵌入 arXiv 搜索语法。

    参数：
        value：来自已校验 QueryIntent 的单个检索词。
    返回：
        str：压缩空白并移除双引号后的纯文本词语。
    """
    return " ".join(value.replace('"', " ").split())  # 移除语法引号以防止用户文本改变字段或布尔表达式。


def _atom_tag(local_name: str) -> str:
    """构造指定 Atom 标准元素的完整命名空间标签。"""
    return f"{{{ATOM_NAMESPACE}}}{local_name}"  # 统一处理 ElementTree 所需的命名空间标签格式。


def _arxiv_tag(local_name: str) -> str:
    """构造指定 arXiv 扩展元素的完整命名空间标签。"""
    return f"{{{ARXIV_NAMESPACE}}}{local_name}"  # 统一处理 ElementTree 所需的扩展标签格式。


def _element_text(element: ElementTree.Element, local_name: str) -> str | None:
    """读取并压缩指定 Atom 子元素的可选文本。"""
    child = element.find(_atom_tag(local_name))  # 查找当前条目下的标准 Atom 子元素。
    return _normalize_xml_text(child.text) if child is not None else None  # 缺失元素时返回空值而非抛出解析异常。


def _arxiv_element_text(element: ElementTree.Element, local_name: str) -> str | None:
    """读取并压缩指定 arXiv 扩展子元素的可选文本。"""
    child = element.find(_arxiv_tag(local_name))  # 查找当前条目下的 arXiv 扩展子元素。
    return _normalize_xml_text(child.text) if child is not None else None  # 缺失扩展字段时返回空值。


def _required_element_text(element: ElementTree.Element, local_name: str) -> str:
    """读取论文条目必须存在的 Atom 文本字段。"""
    text_value = _element_text(element, local_name)  # 读取并规范化必填 Atom 字段。
    if text_value is None:  # 标题或标识缺失时无法构造稳定论文记录。
        raise ArxivMappingError(f"arXiv 条目缺少有效字段：{local_name}")  # 返回可定位但不含原始响应的映射错误。
    return text_value  # 返回已经通过空值校验的文本。


def _normalize_xml_text(value: str | None) -> str | None:
    """压缩 Atom XML 文本中的换行和多余空白。"""
    normalized_text = " ".join(value.split()) if value else ""  # 将 XML 格式化空白统一为单个空格。
    return normalized_text or None  # 将空字符串统一视为缺失。


def _extract_arxiv_id(abstract_url: str) -> str:
    """从 arXiv 抽象页 URL 提取不含版本号的稳定 arXiv 标识。"""
    path = unquote(urlparse(abstract_url).path).strip("/")  # 解析 URL 路径并去除前后分隔符。
    identifier = path.removeprefix("abs/")  # 移除官方抽象页固定路径前缀。
    identifier = ARXIV_IDENTIFIER_VERSION_PATTERN.sub("", identifier)  # 去除可变版本号以支持版本族和跨源去重。
    if not identifier:  # 空路径或非论文 URL 不能构造稳定来源标识。
        raise ArxivMappingError("arXiv 条目缺少有效论文标识")  # 返回不包含完整响应的安全映射错误。
    return identifier  # 返回现代或旧式 arXiv 的无版本稳定标识。


def _extract_year(published_text: str | None) -> int | None:
    """从 Atom 首版投稿时间提取可展示的四位年份。"""
    year_text = published_text[:4] if published_text else ""  # 仅读取 ISO 8601 时间戳前四位年份字符。
    return int(year_text) if year_text.isdigit() else None  # 遇到异常日期时保持年份未知而非阻断检索。


def _extract_authors(entry: ElementTree.Element) -> list[PaperAuthor]:
    """提取 arXiv 作者名称与可选机构信息。"""
    authors: list[PaperAuthor] = []  # 累积可构造的统一作者记录。
    for author_element in entry.findall(_atom_tag("author")):  # 按来源返回顺序遍历每名作者。
        name_element = author_element.find(_atom_tag("name"))  # 读取 Atom 标准作者名称子元素。
        author_name = _normalize_xml_text(name_element.text) if name_element is not None else None  # 规范化可选作者名称。
        if author_name is None:  # 作者模型要求存在显示名称。
            continue  # 跳过结构异常的作者条目。
        affiliation_element = author_element.find(_arxiv_tag("affiliation"))  # 读取 arXiv 可选机构扩展字段。
        affiliation = _normalize_xml_text(affiliation_element.text) if affiliation_element is not None else None  # 规范化可选机构文本。
        authors.append(PaperAuthor(name=author_name, institution=affiliation))  # 写入无来源作者 ID 的统一作者模型。
    return authors  # 返回保持来源顺序的可用作者列表。


def _extract_categories(entry: ElementTree.Element) -> list[str]:
    """提取并去重 arXiv 条目中的分类关键词。"""
    categories: list[str] = []  # 保持来源顺序收集分类术语。
    for category in entry.findall(_atom_tag("category")):  # 遍历 Atom 标准分类元素。
        term = _normalize_xml_text(category.get("term"))  # 读取分类元素的 term 属性。
        if term and term not in categories:  # 忽略空值与重复分类。
            categories.append(term)  # 保留可用于展示和领域路由的分类术语。
    return categories  # 返回保持来源顺序的去重分类列表。


def _extract_open_access_url(entry: ElementTree.Element, fallback_url: str) -> str:
    """优先提取 arXiv 返回的公开 PDF 链接，缺失时回退抽象页。"""
    for link in entry.findall(_atom_tag("link")):  # 遍历来源声明的全部关联链接。
        href = _normalize_xml_text(link.get("href"))  # 读取并规范化候选公开链接。
        link_type = _normalize_xml_text(link.get("type"))  # 读取可选 MIME 类型。
        if href and link_type == "application/pdf":  # 优先选择来源明确标记的公开 PDF。
            return href  # 返回可直接用于界面访问的 PDF 链接。
    return fallback_url  # 未提供 PDF 时保留公开抽象页而不虚构下载地址。
