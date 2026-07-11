"""封装 Semantic Scholar 论文搜索、节流和统一论文映射。"""

import asyncio  # 串行控制来源级请求间隔。
from collections.abc import Mapping  # 安全读取嵌套 JSON 对象。

import httpx  # 提供异步 HTTP 客户端和可注入测试传输层。

from backend.app.adapters.base import AcademicSearchAdapter  # 实现 LangGraph 可替换的统一适配器协议。
from backend.app.core.config import Settings, settings  # 读取 Semantic Scholar 地址、密钥、超时和限额配置。
from backend.app.core.logging import logger  # 记录不含查询与密钥的来源调用统计。
from backend.app.models.paper import PaperAuthor, PaperRecord, PaperSourceRecord  # 构造保留来源溯源信息的统一论文记录。
from backend.app.models.query_intent import QueryIntent  # 接收查询规划节点输出的统一意图。


SEMANTIC_SCHOLAR_PAPER_FIELDS = (  # 仅请求当前统一模型与溯源需要的最小字段集合。
    "paperId",  # 获取 Semantic Scholar 来源内稳定论文标识。
    "externalIds",  # 获取 DOI、arXiv、PMID 和 DBLP 等跨来源标识。
    "title",  # 获取论文展示和过滤所需标题。
    "abstract",  # 获取语义排序和条件核验所需摘要。
    "authors",  # 获取作者显示名称和来源作者标识。
    "year",  # 获取时间过滤和展示所需年份。
    "venue",  # 获取期刊或会议展示字段。
    "citationCount",  # 获取基础质量信号。
    "references.paperId",  # 获取真实引文关系的上游来源标识。
    "isOpenAccess",  # 获取开放获取状态。
    "openAccessPdf",  # 获取来源提供的合法开放访问链接。
    "publicationTypes",  # 获取论文类型的基础分类。
)


class SemanticScholarMappingError(ValueError):
    """表示 Semantic Scholar 响应缺少生成统一论文所必需的数据。"""


class SemanticScholarClientError(RuntimeError):
    """表示 Semantic Scholar 调用、限流或响应结构不可用。"""


def build_semantic_scholar_search_params(query: QueryIntent) -> dict[str, str | int]:
    """将 QueryIntent 转换为 Semantic Scholar 单页搜索请求参数。

    参数：
        query：已校验的结构化检索意图。
    返回：
        dict[str, str | int]：不含密钥、可由 HTTP 客户端直接使用的请求参数。
    """
    search_terms: list[str] = []  # 按确定顺序收集可用于来源全文检索的结构化词。
    for terms in (query.research_topics, query.methods, query.tasks, query.datasets, query.must_include):  # 合并主题、方法、任务、数据集和硬约束。
        search_terms.extend(term.strip() for term in terms if term.strip())  # 跳过空白项并保持 QueryIntent 的语义顺序。
    search_text = " ".join(search_terms) or query.normalized_query.strip()  # 缺少显式词时回退到必填的规范化查询文本。
    return {  # 返回官方 /paper/search 端点所需的单页参数。
        "query": search_text,  # 使用不支持特殊语法的纯文本查询。
        "limit": query.target_paper_count,  # 限制首版单页返回规模以控制成本。
        "fields": ",".join(SEMANTIC_SCHOLAR_PAPER_FIELDS),  # 限制响应字段减少带宽和解析成本。
    }


class SemanticScholarClient(AcademicSearchAdapter):
    """实现 Semantic Scholar 单页论文搜索与 1 RPS 来源级节流。

    参数：
        settings_override：测试或多环境场景下可替换的配置对象。
        transport：可选 HTTP 传输层，仅用于无网络单元测试或定制网络策略。
    """

    source = "semantic_scholar"  # 声明当前客户端实现的统一来源名称。

    def __init__(
        self,
        settings_override: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """保存配置、测试传输层和来源级节流状态。"""
        self._settings = settings_override or settings  # 默认复用经环境变量校验的全局配置。
        self._transport = transport  # 保留可由测试替换的 HTTP 传输层。
        self._rate_limit_lock = asyncio.Lock()  # 串行化同一客户端的请求起始时间。
        self._next_request_at = 0.0  # 保存下一次允许发起请求的事件循环时间。

    async def search(self, query: QueryIntent) -> list[PaperRecord]:
        """搜索 Semantic Scholar 并返回保留来源排名的统一论文记录。

        参数：
            query：包含检索词、目标数量和约束的完整查询意图。
        返回：
            list[PaperRecord]：已映射且保留来源溯源信息的论文列表。
        异常：
            SemanticScholarClientError：HTTP、网络或响应结构异常时抛出。
        """
        await self._wait_for_rate_limit()  # 在请求前遵守配置化的来源级最小间隔。
        params = build_semantic_scholar_search_params(query)  # 构造不含密钥的可测试请求参数。
        headers = self._build_headers()  # 仅在配置了密钥时构造认证请求头。
        data = await self._request_search_data(params, headers)  # 执行带有限流重试和安全错误分类的来源请求。

        papers: list[PaperRecord] = []  # 保存成功映射的统一多源论文记录。
        skipped_count = 0  # 统计字段不完整而无法映射的单条结果数量。
        for raw_rank, result in enumerate(data, start=1):  # 保留来源返回顺序作为 RRF 所需的原始排名。
            paper_data = _as_mapping(result)  # 确认单条论文结果具有对象结构。
            if paper_data is None:  # 非对象条目不能映射为统一论文。
                skipped_count += 1  # 累加异常条目统计。
                continue  # 继续处理同页其余论文。
            try:  # 单条映射失败不应丢弃整页可用结果。
                papers.append(map_semantic_scholar_paper(paper_data, raw_rank=raw_rank))  # 映射并保留来源原始排名。
            except SemanticScholarMappingError:  # 仅跳过缺少必要标识或标题的结果。
                skipped_count += 1  # 累加映射失败统计。

        logger.info("Semantic Scholar 检索完成：原始结果=%d，映射成功=%d，跳过=%d", len(data), len(papers), skipped_count)  # 记录不含完整查询的阶段统计。
        return papers  # 返回可直接进入多源融合的统一论文记录。

    async def _request_search_data(self, params: dict[str, str | int], headers: dict[str, str]) -> list[object]:
        """请求论文数组，并仅对明确限流响应执行配置化短重试。

        参数：
            params：不含密钥的 Semantic Scholar 搜索参数。
            headers：仅可能包含 x-api-key 的认证请求头。
        返回：
            list[object]：官方 data 论文数组。
        异常：
            SemanticScholarClientError：网络、非限流状态、认证、参数或响应结构异常。
        """
        max_attempts = self._settings.semantic_scholar_max_retries + 1  # 将重试次数转换为包含首次调用的总尝试数。
        async with httpx.AsyncClient(  # 在全部尝试间复用连接池并在结束时自动关闭。
            base_url=self._settings.semantic_scholar_api_base_url,  # 使用集中配置的 Graph API 地址。
            timeout=self._settings.semantic_scholar_timeout_seconds,  # 使用集中配置的请求超时。
            transport=self._transport,  # 在测试时使用本地 MockTransport。
        ) as client:
            for attempt in range(1, max_attempts + 1):  # 按首次调用和有限重试顺序执行。
                try:  # 将 HTTP 层异常转换为不泄露正文和请求头的领域错误。
                    response = await client.get("/paper/search", params=params, headers=headers)  # 请求官方单页论文搜索端点。
                    response.raise_for_status()  # 将非成功 HTTP 状态转换为可分类异常。
                    payload = response.json()  # 解码 JSON 响应供结构校验和映射使用。
                except httpx.HTTPStatusError as error:  # 单独处理状态码并允许 429 重试。
                    status_code = error.response.status_code  # 提取不含认证信息的状态码。
                    if status_code == 429 and attempt < max_attempts:  # 仅在仍有预算时重试明确限流。
                        await self._wait_before_retry(attempt, max_attempts)  # 按来源 RPS 等待后继续。
                        continue  # 发起下一次有限尝试。
                    logger.error("Semantic Scholar 请求失败，状态码=%d，尝试=%d/%d", status_code, attempt, max_attempts)  # 记录状态与尝试次数。
                    raise SemanticScholarClientError(f"Semantic Scholar 请求失败（HTTP {status_code}）") from None  # 隐藏底层请求上下文。
                except httpx.RequestError as error:  # 网络和超时错误不自动重试，避免放大外部故障。
                    logger.error("Semantic Scholar 网络请求失败，错误类型=%s", type(error).__name__)  # 仅记录安全异常类型。
                    raise SemanticScholarClientError("Semantic Scholar 网络请求失败") from None  # 返回稳定领域错误。
                except ValueError:  # 捕获无效 JSON 等解析失败。
                    logger.error("Semantic Scholar 响应不是有效 JSON")  # 不记录原始响应正文。
                    raise SemanticScholarClientError("Semantic Scholar 响应格式无效") from None  # 返回不泄露内部细节的错误。
                response_data = _as_mapping(payload)  # 确认根 JSON 响应具有对象结构。
                data = response_data.get("data") if response_data else None  # 读取官方搜索响应中的论文数组。
                if isinstance(data, list):  # 正常响应立即结束重试循环。
                    return data  # 返回官方论文数组供单条映射。
                error_category = _classify_error_envelope(response_data)  # 将非标准成功状态响应归类为安全错误摘要。
                if error_category == "请求受限" and attempt < max_attempts:  # 兼容供应商以 HTTP 200 返回的限流信封。
                    await self._wait_before_retry(attempt, max_attempts)  # 等待来源级间隔后继续。
                    continue  # 发起下一次有限尝试。
                response_fields = ",".join(sorted(response_data.keys())) if response_data else "none"  # 仅记录字段名而不记录值。
                logger.error("Semantic Scholar 响应不可用：错误类型=%s，响应字段=%s，尝试=%d/%d", error_category, response_fields, attempt, max_attempts)  # 记录安全分类和最终尝试数。
                raise SemanticScholarClientError(f"Semantic Scholar {error_category}")  # 将来源错误安全传递给协调器。
        raise SemanticScholarClientError("Semantic Scholar 请求受限")  # 防御循环异常退出，正常路径不会到达此处。

    async def _wait_before_retry(self, attempt: int, max_attempts: int) -> None:
        """按已配置来源速率等待下一次限流重试。"""
        wait_seconds = max(1.0, 1.0 / self._settings.semantic_scholar_requests_per_second)  # 至少等待一秒并遵守更低 RPS。
        logger.warning("Semantic Scholar 请求受限，等待重试：秒数=%.3f，下一次=%d/%d", wait_seconds, attempt + 1, max_attempts)  # 记录可观测重试进度。
        await asyncio.sleep(wait_seconds)  # 让出事件循环且不阻塞其他来源任务。

    def _build_headers(self) -> dict[str, str]:
        """构造可选 API Key 请求头，未配置密钥时保持匿名访问。

        返回：
            dict[str, str]：不含空密钥字段的 HTTP 请求头。
        """
        if self._settings.semantic_scholar_api_key is None:  # 官方端点允许匿名访问，但可能受共享限流影响。
            return {}  # 不发送空认证头，避免产生误导性请求。
        return {"x-api-key": self._settings.semantic_scholar_api_key.get_secret_value()}  # 仅在实际请求层解封装密钥。

    async def _wait_for_rate_limit(self) -> None:
        """按配置化 RPS 串行等待下一次允许的来源请求时间。"""
        async with self._rate_limit_lock:  # 防止同一客户端并发请求绕过来源级限额。
            loop = asyncio.get_running_loop()  # 使用事件循环单调时间避免系统时钟调整影响间隔。
            now = loop.time()  # 读取当前单调时间。
            wait_seconds = max(0.0, self._next_request_at - now)  # 计算距离允许发起请求还需等待的时间。
            if wait_seconds > 0:  # 仅在并发或连续调用过快时等待。
                logger.info("Semantic Scholar 限流等待：秒数=%.3f", wait_seconds)  # 记录来源级等待统计，不记录查询内容。
                await asyncio.sleep(wait_seconds)  # 让出事件循环并遵守来源最小间隔。
            self._next_request_at = loop.time() + (1.0 / self._settings.semantic_scholar_requests_per_second)  # 预约下一次允许请求的时间。


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将 JSON 值安全转换为可读取的字符串键映射。

    参数：
        value：待检查的 JSON 字段值。
    返回：
        Mapping[str, object] | None：可用对象或空值。
    """
    return value if isinstance(value, Mapping) else None  # 拒绝列表、字符串和空值等非对象字段。


def _classify_error_envelope(response_data: Mapping[str, object] | None) -> str:
    """根据供应商错误信封中的公开提示归类原因，但不返回或记录原始正文。

    参数：
        response_data：HTTP 成功状态下缺少 data 的顶层 JSON 对象。
    返回：
        str：认证失败、请求受限、请求参数被拒绝、供应商暂时不可用或响应结构无效。
    """
    if response_data is None:  # 非对象响应无法进一步判断供应商错误。
        return "响应结构无效"  # 返回稳定结构分类。
    candidate_values = [response_data.get(field_name) for field_name in ("message", "error", "detail")]  # 只检查常见公开错误字段。
    message_text = " ".join(value for value in candidate_values if isinstance(value, str)).casefold()  # 合并用于本地关键词分类但不写入日志。
    if any(marker in message_text for marker in ("429", "rate limit", "too many", "throttl")):  # 识别限流和共享匿名额度提示。
        return "请求受限"  # 指示调用方稍后重试而非修改查询。
    if any(marker in message_text for marker in ("401", "403", "api key", "unauthor", "forbidden", "authenticat")):  # 识别无效密钥或权限不足。
        return "认证失败"  # 指示部署方检查 API Key。
    if any(marker in message_text for marker in ("400", "field", "parameter", "query", "invalid", "bad request")):  # 识别字段和查询参数拒绝。
        return "请求参数被拒绝"  # 指示适配器契约可能需要调整。
    if any(marker in message_text for marker in ("500", "502", "503", "504", "internal", "unavailable", "timeout")):  # 识别供应商服务故障。
        return "供应商暂时不可用"  # 指示安全降级并稍后重试。
    return "响应结构无效"  # 未知信封不猜测具体原因。


def _optional_text(value: object) -> str | None:
    """提取去除首尾空白后的可选文本字段。

    参数：
        value：待转换的 JSON 字段值。
    返回：
        str | None：有效文本或空值。
    """
    if not isinstance(value, str):  # 非字符串不能作为统一模型文本字段。
        return None  # 以空值表示缺失或类型异常。
    normalized_text = value.strip()  # 去除来源可能带入的展示空白。
    return normalized_text or None  # 将空字符串统一视为缺失。


def _required_text(paper_data: Mapping[str, object], field_name: str) -> str:
    """读取 Semantic Scholar 论文响应的必要文本字段。

    参数：
        paper_data：单条论文 JSON 对象。
        field_name：必须存在的字段名。
    返回：
        str：已校验的字段文本。
    异常：
        SemanticScholarMappingError：字段缺失、类型错误或为空时抛出。
    """
    text_value = _optional_text(paper_data.get(field_name))  # 读取并规范化必要字段。
    if text_value is None:  # 必要字段缺失时无法构造稳定论文记录。
        raise SemanticScholarMappingError(f"Semantic Scholar 论文缺少有效字段：{field_name}")  # 返回可定位但不含原始响应的错误。
    return text_value  # 返回已通过校验的字段文本。


def _extract_authors(paper_data: Mapping[str, object]) -> list[PaperAuthor]:
    """提取作者名称及 Semantic Scholar 作者标识。

    参数：
        paper_data：单条论文 JSON 对象。
    返回：
        list[PaperAuthor]：保持来源顺序的规范化作者列表。
    """
    raw_authors = paper_data.get("authors")  # 读取来源返回的作者数组。
    if not isinstance(raw_authors, list):  # 缺失作者数组不应阻断论文元数据使用。
        return []  # 返回空作者列表表示来源未提供信息。
    authors: list[PaperAuthor] = []  # 累积可构造的统一作者记录。
    for raw_author in raw_authors:  # 按来源返回顺序处理作者。
        author_data = _as_mapping(raw_author)  # 确认单条作者条目是对象。
        if author_data is None:  # 跳过结构异常的作者条目。
            continue  # 保留同一论文中其余可用作者。
        author_name = _optional_text(author_data.get("name"))  # 提取作者显示名称。
        if author_name is None:  # 作者模型要求存在名称。
            continue  # 跳过无法展示和匹配的作者。
        author_id = _optional_text(author_data.get("authorId"))  # 提取可选的来源作者标识。
        authors.append(  # 写入统一作者模型。
            PaperAuthor(  # 构造包含可选来源标识的作者记录。
                name=author_name,  # 使用已校验的作者显示名称。
                source_author_ids={"semantic_scholar": author_id} if author_id else {},  # 仅在来源提供标识时保留平台作者 ID。
            )
        )
    return authors  # 返回保持来源顺序的作者列表。


def _extract_references(paper_data: Mapping[str, object]) -> list[str]:
    """提取 Semantic Scholar 返回的真实引用论文标识。

    参数：
        paper_data：单条论文 JSON 对象。
    返回：
        list[str]：保持来源顺序的有效引用论文标识列表。
    """
    raw_references = paper_data.get("references")  # 读取来源返回的引用数组。
    if not isinstance(raw_references, list):  # 缺失引用数组属于允许的部分元数据。
        return []  # 返回空引用列表而不是阻断检索。
    references: list[str] = []  # 累积可用于后续图谱扩展的引用标识。
    for raw_reference in raw_references:  # 遍历每条引用关系对象。
        reference_data = _as_mapping(raw_reference)  # 确认引用条目具有对象结构。
        paper_id = _optional_text(reference_data.get("paperId")) if reference_data else None  # 读取引用论文的来源标识。
        if paper_id is not None:  # 仅保留有效的引用论文标识。
            references.append(paper_id)  # 保持来源返回顺序写入引用列表。
    return references  # 返回可用于真实引文图的来源标识。


def _extract_open_access_url(paper_data: Mapping[str, object]) -> str | None:
    """从开放访问 PDF 对象提取可选的合法链接文本。

    参数：
        paper_data：单条论文 JSON 对象。
    返回：
        str | None：来源提供的开放访问链接或空值。
    """
    open_access_pdf = _as_mapping(paper_data.get("openAccessPdf"))  # 读取来源返回的开放访问 PDF 对象。
    return _optional_text(open_access_pdf.get("url")) if open_access_pdf else None  # 返回可选链接且不构造来源未提供的 URL。


def _map_paper_type(paper_data: Mapping[str, object]) -> str | None:
    """将 Semantic Scholar 论文类型数组映射为首版支持的基础类型。

    参数：
        paper_data：单条论文 JSON 对象。
    返回：
        str | None：可识别的 article、conference、preprint、review 或空值。
    """
    publication_types = paper_data.get("publicationTypes")  # 读取来源提供的论文类型数组。
    if not isinstance(publication_types, list):  # 缺失或异常类型数组不应阻断论文映射。
        return None  # 以空值表示来源未提供可识别类型。
    normalized_types = {_optional_text(item).casefold() for item in publication_types if _optional_text(item) is not None}  # 规范化有效类型用于映射。
    if "conference" in normalized_types:  # 优先识别会议论文。
        return "conference"  # 返回统一会议论文类型。
    if "review" in normalized_types:  # 识别综述论文。
        return "review"  # 返回统一综述论文类型。
    if "article" in normalized_types or "journalarticle" in normalized_types:  # 识别期刊或通用文章论文。
        return "article"  # 返回统一文章论文类型。
    return "preprint" if "preprint" in normalized_types else None  # 仅在明确标记时返回预印本类型。


def map_semantic_scholar_paper(paper_data: Mapping[str, object], raw_rank: int | None = None) -> PaperRecord:
    """将一条 Semantic Scholar 论文响应映射为可溯源的 PaperRecord。

    参数：
        paper_data：已经由 HTTP 客户端解码的单条论文 JSON 对象。
        raw_rank：该论文在当前来源搜索结果中的一开始排名。
    返回：
        PaperRecord：可进入多源融合和后续排序的规范化论文记录。
    异常：
        SemanticScholarMappingError：缺少有效 paperId 或 title 时抛出。
    """
    external_ids = _as_mapping(paper_data.get("externalIds"))  # 读取可选的跨来源标识对象。
    citation_count = paper_data.get("citationCount")  # 读取来源提供的引用次数。
    publication_year = paper_data.get("year")  # 读取可选发表年份。
    paper_id = _required_text(paper_data, "paperId")  # 使用 Semantic Scholar 主标识构造统一记录。
    return PaperRecord(  # 构造并交由 Pydantic 二次校验的多源论文记录。
        paper_id=paper_id,  # 保持当前模型兼容的论文稳定标识。
        title=_required_text(paper_data, "title"),  # 使用来源规定的论文标题字段。
        abstract=_optional_text(paper_data.get("abstract")) or "",  # 缺失摘要时保留空字符串以支持部分元数据。
        authors=_extract_authors(paper_data),  # 规范化作者名称与来源作者标识。
        year=publication_year if isinstance(publication_year, int) and not isinstance(publication_year, bool) else None,  # 忽略异常年份类型。
        venue=_optional_text(paper_data.get("venue")),  # 保留可选期刊或会议名称。
        doi=_optional_text(external_ids.get("DOI")) if external_ids else None,  # 映射跨来源 DOI 标识。
        arxiv_id=_optional_text(external_ids.get("ArXiv")) if external_ids else None,  # 映射预印本 arXiv 标识。
        pmid=_optional_text(external_ids.get("PubMed")) if external_ids else None,  # 映射医学文献 PubMed 标识。
        citation_count=max(citation_count, 0) if isinstance(citation_count, int) and not isinstance(citation_count, bool) else 0,  # 防御负数或异常引用计数。
        references=_extract_references(paper_data),  # 保留来源返回的真实引用论文标识。
        source="semantic_scholar",  # 标记当前统一记录的主来源。
        paper_type=_map_paper_type(paper_data),  # 映射可识别的基础论文类型。
        semantic_scholar_id=paper_id,  # 显式保留 Semantic Scholar 来源标识。
        dblp_key=_optional_text(external_ids.get("DBLP")) if external_ids else None,  # 映射可选 DBLP 键。
        is_open_access=paper_data.get("isOpenAccess") if isinstance(paper_data.get("isOpenAccess"), bool) else None,  # 保留三态开放获取信息。
        open_access_url=_extract_open_access_url(paper_data),  # 保留来源提供的开放访问链接。
        source_records=[PaperSourceRecord(source="semantic_scholar", external_id=paper_id, raw_rank=raw_rank)],  # 写入来源溯源和原始排名。
    )
