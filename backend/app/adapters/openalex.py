"""将 OpenAlex Work JSON 响应转换为 ScholarFlow 的统一论文模型。"""

from collections.abc import Mapping  # 安全识别嵌套 JSON 对象。

import httpx  # 提供异步 HTTP 客户端和可注入测试传输层。

from backend.app.core.config import Settings, settings  # 读取 OpenAlex 地址、密钥和超时配置。
from backend.app.core.logging import logger  # 记录不含敏感信息的调用统计和错误。
from backend.app.models.paper import Paper, PaperAuthor  # 复用统一的论文领域模型。
from backend.app.models.query import QuerySchema  # 读取结构化检索约束。


OPENALEX_WORK_FIELDS = (  # 声明映射器需要的最小 Work 字段集合。
    "id",  # 获取来源内稳定论文标识。
    "doi",  # 获取跨来源 DOI 标识。
    "title",  # 获取论文标题。
    "publication_year",  # 获取年份过滤和展示字段。
    "cited_by_count",  # 获取基础质量信号。
    "abstract_inverted_index",  # 获取可还原的摘要。
    "authorships",  # 获取作者和机构信息。
    "primary_location",  # 获取期刊或会议名称。
    "referenced_works",  # 获取引文图谱关系。
    "ids",  # 兼容嵌套外部标识。
)


class OpenAlexMappingError(ValueError):
    """表示 OpenAlex 响应缺少生成统一论文所必需的数据。"""


class OpenAlexClientError(RuntimeError):
    """表示 OpenAlex HTTP 调用或响应结构不可用。"""


def build_openalex_work_params(query: QuerySchema) -> dict[str, str | int]:
    """将结构化查询转换为 OpenAlex /works 的纯请求参数。

    参数：
        query：已经通过 QuerySchema 校验的检索约束。
    返回：
        dict[str, str | int]：不含密钥、可直接传给未来 HTTP 客户端的参数。
    异常：
        ValueError：没有可用于 OpenAlex 全文搜索的关键词时抛出。
    """
    search_terms: list[str] = []  # 按确定顺序收集可送入全文搜索的关键词。
    for terms in (query.topic, query.method, query.dataset, query.domain, query.must_include):  # 合并可表达研究意图的字段。
        search_terms.extend(term.strip() for term in terms if term.strip())  # 去除空白项并保留用户语义顺序。
    if not search_terms:  # OpenAlex 搜索必须有至少一个明确检索词。
        raise ValueError("QuerySchema 至少需要一个主题、方法、数据集、领域或必须包含关键词")  # 避免发起无约束高成本搜索。

    params: dict[str, str | int] = {  # 初始化未来 HTTP 客户端所需的基础参数。
        "search": " ".join(search_terms),  # 使用 OpenAlex 全文搜索表达结构化意图。
        "sort": "relevance_score:desc",  # 优先返回与搜索词最相关的论文。
        "per_page": query.target_count,  # 将目标数量限制为 API 单页返回数量。
        "select": ",".join(OPENALEX_WORK_FIELDS),  # 仅请求统一映射器实际需要的字段。
    }
    if query.year_range:  # 仅在用户明确指定年份范围时添加 API 过滤。
        params["filter"] = f"publication_year:{query.year_range[0]}-{query.year_range[1]}"  # 使用 OpenAlex 年份范围过滤语法。
    return params  # 排除尚未解析为来源 ID 的 venue 和后续本地处理的 exclude 条件。


class OpenAlexClient:
    """封装 OpenAlex /works 请求、响应校验和论文映射。

    参数：
        settings_override：测试或多环境场景下可替换的配置对象。
        transport：可选 HTTP 传输层，仅用于无网络单元测试或定制网络策略。
    """

    def __init__(
        self,
        settings_override: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """保存客户端配置与可选的 HTTP 传输层。"""
        self._settings = settings_override or settings  # 默认复用经过环境变量校验的全局配置。
        self._transport = transport  # 保留可由测试替换的 HTTP 传输层。

    async def search_works(self, query: QuerySchema) -> list[Paper]:
        """请求 OpenAlex /works 并返回成功映射的论文列表。

        参数：
            query：包含搜索词和过滤条件的结构化查询。
        返回：
            list[Paper]：已规范化且跳过无效单条响应的论文列表。
        异常：
            OpenAlexClientError：网络、HTTP 状态、密钥配置或响应结构异常时抛出。
            ValueError：没有可用于检索的关键词时由参数构造器抛出。
        """
        params = build_openalex_work_params(query)  # 先生成不含密钥的安全请求参数。
        try:  # 将缺失的部署配置转换为适配层领域错误。
            params["api_key"] = self._settings.require_openalex_api_key()  # 在真正请求前注入并校验密钥。
        except ValueError:  # 不将环境变量内容或配置实现细节暴露给上层。
            logger.error("OpenAlex 服务未配置 API 密钥")  # 记录安全的部署错误信息。
            raise OpenAlexClientError("OpenAlex 服务尚未配置") from None  # 返回稳定且不含密钥的领域错误。
        try:  # 捕获 HTTP 层异常并转换为不会暴露 URL 参数的领域错误。
            async with httpx.AsyncClient(  # 为单次请求创建可自动关闭的异步客户端。
                base_url=self._settings.openalex_api_base_url,  # 使用集中配置的 OpenAlex 地址。
                timeout=self._settings.openalex_timeout_seconds,  # 使用集中配置的请求超时。
                transport=self._transport,  # 在测试时使用本地 MockTransport。
            ) as client:
                response = await client.get("/works", params=params)  # 请求论文列表端点。
                response.raise_for_status()  # 将非成功 HTTP 状态转换为异常。
                payload = response.json()  # 解码 JSON 响应供后续结构校验。
        except httpx.HTTPStatusError as error:  # 单独记录安全的状态码而不记录含密钥 URL。
            logger.error("OpenAlex 请求失败，状态码=%d", error.response.status_code)  # 输出可观测但不含敏感信息的错误。
            raise OpenAlexClientError(f"OpenAlex 请求失败（HTTP {error.response.status_code}）") from None  # 隐藏原始请求 URL。
        except httpx.RequestError as error:  # 捕获超时、连接和传输错误。
            logger.error("OpenAlex 网络请求失败，错误类型=%s", type(error).__name__)  # 仅记录安全的异常类型。
            raise OpenAlexClientError("OpenAlex 网络请求失败") from None  # 避免异常链泄露请求参数。
        except ValueError:  # 保留 JSON 解码错误给下方统一响应结构处理。
            logger.error("OpenAlex 响应不是有效 JSON")  # 记录不包含响应正文的解析错误。
            raise OpenAlexClientError("OpenAlex 响应格式无效") from None  # 返回稳定的领域错误。

        response_data = _as_mapping(payload)  # 确认响应根对象是 JSON 映射。
        results = response_data.get("results") if response_data else None  # 读取 OpenAlex 列表响应的结果数组。
        if not isinstance(results, list):  # 缺少结果数组代表 API 响应结构与预期不符。
            logger.error("OpenAlex 响应缺少 results 数组")  # 记录可定位的结构异常。
            raise OpenAlexClientError("OpenAlex 响应缺少 results 数组")  # 阻止错误数据进入后续排序流程。

        papers: list[Paper] = []  # 保存成功映射的统一论文。
        skipped_count = 0  # 统计字段不完整而被跳过的单条 Work 数。
        for result in results:  # 按 OpenAlex 返回顺序处理 Work 记录。
            work = _as_mapping(result)  # 确认单条结果具有对象结构。
            if work is None:  # 非对象结果无法映射为论文。
                skipped_count += 1  # 记录异常条目数量。
                continue  # 继续处理其余结果。
            try:  # 单条 Work 失败不应丢弃整页有效结果。
                papers.append(map_openalex_work_to_paper(work))  # 映射并保存有效论文。
            except OpenAlexMappingError:  # 仅跳过缺失必要字段的 Work。
                skipped_count += 1  # 记录映射失败数量。

        logger.info("OpenAlex 检索完成：原始结果=%d，映射成功=%d，跳过=%d", len(results), len(papers), skipped_count)  # 记录检索阶段统计。
        return papers  # 返回可供去重服务处理的规范化论文列表。


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将 JSON 值安全转换为字符串键的映射对象。

    参数：
        value：待检查的 JSON 字段值。
    返回：
        Mapping[str, object] | None：可用映射或空值。
    """
    return value if isinstance(value, Mapping) else None  # 拒绝列表、字符串和空值等非对象字段。


def _optional_text(value: object) -> str | None:
    """提取去除首尾空白后的非空文本。

    参数：
        value：待转换的 JSON 字段值。
    返回：
        str | None：有效文本或空值。
    """
    if not isinstance(value, str):  # 非字符串不能作为统一文本字段。
        return None  # 以空值表示字段缺失或类型异常。
    normalized_text = value.strip()  # 去除数据源可能带入的展示空白。
    return normalized_text or None  # 将空字符串统一视为缺失。


def _required_text(work: Mapping[str, object], field_name: str) -> str:
    """读取 OpenAlex Work 的必要文本字段。

    参数：
        work：OpenAlex Work JSON 对象。
        field_name：必须存在的字段名。
    返回：
        str：已校验的字段文本。
    异常：
        OpenAlexMappingError：字段缺失、类型错误或为空时抛出。
    """
    text_value = _optional_text(work.get(field_name))  # 读取并规范化必要字段。
    if text_value is None:  # 必要字段无法用于构造统一模型时立即失败。
        raise OpenAlexMappingError(f"OpenAlex Work 缺少有效字段：{field_name}")  # 返回可定位的映射错误。
    return text_value  # 返回已通过校验的文本。


def _restore_abstract(work: Mapping[str, object]) -> str:
    """根据 OpenAlex 的摘要倒排索引还原阅读顺序文本。

    参数：
        work：OpenAlex Work JSON 对象。
    返回：
        str：还原后的摘要；字段缺失或结构异常时为空字符串。
    """
    inverted_index = _as_mapping(work.get("abstract_inverted_index"))  # 读取 OpenAlex 的词语位置映射。
    if inverted_index is None:  # OpenAlex 可以合法地不提供摘要。
        return ""  # 以空摘要保持论文记录可用。
    words_by_position: dict[int, str] = {}  # 根据词语位置重建原始顺序。
    for word, positions in inverted_index.items():  # 遍历每个词语与其出现位置。
        if not isinstance(word, str) or not isinstance(positions, list):  # 跳过不符合 API 结构的条目。
            continue  # 避免单个异常字段阻断整篇论文映射。
        for position in positions:  # 处理词语在摘要中的每次出现。
            if isinstance(position, int) and position >= 0:  # 仅接受合法的非负位置。
                words_by_position.setdefault(position, word)  # 保留首次出现的词语以处理异常重复位置。
    return " ".join(words_by_position[position] for position in sorted(words_by_position))  # 按位置排序生成摘要文本。


def _extract_authors(work: Mapping[str, object]) -> list[PaperAuthor]:
    """从 OpenAlex authorships 字段提取规范化作者列表。

    参数：
        work：OpenAlex Work JSON 对象。
    返回：
        list[PaperAuthor]：保留可识别作者与首个机构信息的列表。
    """
    authorships = work.get("authorships")  # 读取作者署名数组。
    if not isinstance(authorships, list):  # 缺失作者信息不应阻断论文检索。
        return []  # 以空作者列表表示来源未提供数据。
    authors: list[PaperAuthor] = []  # 累积可构造的作者模型。
    for authorship in authorships:  # 按 OpenAlex 返回顺序处理作者。
        authorship_data = _as_mapping(authorship)  # 将当前署名条目转换为可读取对象。
        if authorship_data is None:  # 忽略结构异常的署名条目。
            continue  # 继续处理其他合法作者。
        author_data = _as_mapping(authorship_data.get("author"))  # 读取嵌套作者对象。
        if author_data is None:  # 缺少作者对象时无法建立作者记录。
            continue  # 忽略该异常署名。
        author_name = _optional_text(author_data.get("display_name"))  # 提取作者显示名称。
        if author_name is None:  # 姓名是作者模型的必要字段。
            continue  # 跳过无法识别的作者。
        institution_name: str | None = None  # 默认不绑定机构信息。
        institutions = authorship_data.get("institutions")  # 读取作者机构数组。
        if isinstance(institutions, list) and institutions:  # 仅在至少存在一个机构时继续解析。
            first_institution = _as_mapping(institutions[0])  # 取首个机构作为简洁的当前归属。
            if first_institution is not None:  # 确认机构对象结构有效。
                institution_name = _optional_text(first_institution.get("display_name"))  # 提取机构显示名称。
        authors.append(  # 写入规范化作者记录。
            PaperAuthor(  # 构造统一作者模型。
                name=author_name,  # 使用必填的作者显示名称。
                orcid=_optional_text(author_data.get("orcid")),  # 保留可选 ORCID。
                institution=institution_name,  # 保留可选首个机构名称。
            )
        )
    return authors  # 返回保持原始作者顺序的列表。


def _extract_venue(work: Mapping[str, object]) -> str | None:
    """从 OpenAlex primary_location 提取期刊或会议名称。

    参数：
        work：OpenAlex Work JSON 对象。
    返回：
        str | None：来源显示名称或空值。
    """
    primary_location = _as_mapping(work.get("primary_location"))  # 读取主发布位置对象。
    if primary_location is None:  # 预印本或不完整元数据可能没有发布位置。
        return None  # 保持期刊字段为空。
    source_data = _as_mapping(primary_location.get("source"))  # 读取主发布位置中的来源对象。
    if source_data is None:  # 缺少来源对象时无法判断期刊或会议。
        return None  # 保持期刊字段为空。
    return _optional_text(source_data.get("display_name"))  # 返回期刊或会议显示名称。


def _extract_references(work: Mapping[str, object]) -> list[str]:
    """从 OpenAlex referenced_works 提取有效的被引论文标识。

    参数：
        work：OpenAlex Work JSON 对象。
    返回：
        list[str]：保持数据源顺序的非空 OpenAlex Work ID 列表。
    """
    references = work.get("referenced_works")  # 读取引文关系数组。
    if not isinstance(references, list):  # 缺失引用列表是合法的部分响应。
        return []  # 使用空列表表示无可用引用关系。
    return [reference for reference in references if _optional_text(reference) is not None]  # 过滤空白或非文本标识。


def map_openalex_work_to_paper(work: Mapping[str, object]) -> Paper:
    """将一条 OpenAlex Work 响应转换为统一论文模型。

    参数：
        work：已经由 HTTP 客户端解码的 OpenAlex Work JSON 对象。
    返回：
        Paper：可被去重、排序和存储模块复用的规范化论文。
    异常：
        OpenAlexMappingError：Work 缺少有效 id 或 title 时抛出。
    """
    identifiers = _as_mapping(work.get("ids"))  # 读取可能包含 DOI 的外部标识对象。
    cited_by_count = work.get("cited_by_count")  # 读取 OpenAlex 提供的引用计数。
    publication_year = work.get("publication_year")  # 读取可选发表年份。
    return Paper(  # 构造统一论文领域模型并交由 Pydantic 二次校验。
        paper_id=_required_text(work, "id"),  # 使用 OpenAlex Work ID 作为来源内稳定标识。
        title=_required_text(work, "title"),  # 使用 API 规定的论文标题字段。
        abstract=_restore_abstract(work),  # 还原倒排索引格式的摘要。
        authors=_extract_authors(work),  # 规范化作者和机构信息。
        year=publication_year if isinstance(publication_year, int) and not isinstance(publication_year, bool) else None,  # 忽略无效年份类型。
        venue=_extract_venue(work),  # 提取主发布位置的期刊或会议名称。
        doi=_optional_text(work.get("doi")) or (_optional_text(identifiers.get("doi")) if identifiers else None),  # 兼容顶层与 ids 中的 DOI。
        pmid=_optional_text(identifiers.get("pmid")) if identifiers else None,  # 保留 ids 中可选的 PubMed 标识。
        citation_count=max(cited_by_count, 0) if isinstance(cited_by_count, int) and not isinstance(cited_by_count, bool) else 0,  # 防御异常负数或类型。
        references=_extract_references(work),  # 保留 OpenAlex 提供的被引 Work ID。
        source="openalex",  # 标记统一模型的当前数据源。
    )
