"""将 OpenAlex Work JSON 响应转换为 ScholarFlow 的统一论文模型。"""

from collections.abc import Mapping  # 安全识别嵌套 JSON 对象。
import re  # 仅用于从供应商错误文本中匹配预定义的安全参数名。
import unicodedata  # 将来源不稳定处理的兼容字符统一为确定性文本。

import httpx  # 提供异步 HTTP 客户端和可注入测试传输层。

from backend.app.core.config import Settings, settings  # 读取 OpenAlex 地址、密钥和超时配置。
from backend.app.core.logging import logger  # 记录不含敏感信息的调用统计和错误。
from backend.app.adapters.base import AcademicSearchAdapter  # 声明当前客户端满足统一学术来源协议。
from backend.app.adapters.academic_api import AcademicApiNetworkError, AcademicApiRequestExecutor  # 复用统一的幂等请求重试、RPS 与冷却边界。
from backend.app.models.paper import Paper, PaperAuthor, PaperRecord, PaperSourceRecord  # 复用基础模型与多源溯源模型。
from backend.app.models.query import QuerySchema  # 读取结构化检索约束。
from backend.app.models.query_intent import QueryIntent  # 接收查询规划节点输出的统一检索意图。
from backend.app.repositories.source_cache import SourceResponseCache, get_source_response_cache  # 复用可降级的来源响应缓存边界。
from backend.app.repositories.source_rate_limiter import SourceCooldownError, SourceRateLimiter  # 将统一冷却状态转换为既有领域异常。


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

_OPENALEX_SAFE_ERROR_PARAMETER_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (  # 仅允许向日志和调用方暴露的请求参数名称及其文本别名。
    ("api_key", ("api_key", "api key")),  # API 密钥仅显示参数名，绝不显示任何值。
    ("filter", ("filter",)),  # OpenAlex 常在 400 中报告过滤表达式问题。
    ("sort", ("sort",)),  # 保留排序参数问题的安全定位能力。
    ("group_by", ("group_by", "group by")),  # 兼容供应商可能使用的下划线和空格名称。
    ("search", ("search",)),  # 仅显示参数名，不回显搜索词。
    ("per_page", ("per_page", "per page")),  # 兼容分页大小参数的常见写法。
    ("page", ("page",)),  # 保留基础页码参数的安全定位能力。
    ("cursor", ("cursor",)),  # 保留深分页游标参数的安全定位能力。
    ("sample", ("sample",)),  # 保留随机抽样参数的安全定位能力。
    ("select", ("select",)),  # 保留字段选择参数的安全定位能力。
)

_OPENALEX_SEARCH_APOSTROPHE_TRANSLATION = str.maketrans("", "", "'‘’")  # 删除英文与智能撇号，避免将所有格分拆为无意义单字母。
_OPENALEX_SEARCH_SEPARATOR_PATTERN = re.compile(r'["“”`´?¿¡!「」『』]+')  # 将引号和问号等非词语分隔符替换为空格，避免来源查询解析器误判。


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

    search_text = normalize_openalex_search_text(" ".join(search_terms))  # 仅在 OpenAlex 请求边界规范化兼容字符与分隔标点。
    params: dict[str, str | int] = {  # 初始化未来 HTTP 客户端所需的基础参数。
        "search": search_text,  # 使用来源兼容的全文搜索文本表达结构化意图。
        "per_page": query.target_count,  # 将目标数量限制为 API 单页返回数量。
        "select": ",".join(OPENALEX_WORK_FIELDS),  # 仅请求统一映射器实际需要的字段以减少响应负担。
    }
    if query.year_range:  # 仅在用户明确指定年份范围时添加 API 过滤。
        params["filter"] = f"publication_year:{query.year_range[0]}-{query.year_range[1]}"  # 使用 OpenAlex 年份范围过滤语法。
    return params  # 排除尚未解析为来源 ID 的 venue 和后续本地处理的 exclude 条件。


def build_openalex_search_params(query: QueryIntent) -> dict[str, str | int]:
    """将 QueryIntent 转换为 OpenAlex /works 的统一适配器请求参数。

    参数：
        query：已由查询规划节点校验的统一检索意图。
    返回：
        dict[str, str | int]：不含密钥、可直接传给 OpenAlex 的请求参数。
    """
    search_terms: list[str] = []  # 按来源无关的确定顺序收集可全文检索的约束词。
    for terms in (query.research_topics, query.methods, query.tasks, query.datasets, query.must_include):  # 合并主题、方法、任务、数据集与硬约束。
        search_terms.extend(term.strip() for term in terms if term.strip())  # 丢弃空白词并保留规划节点给出的顺序。
    search_text = normalize_openalex_search_text(" ".join(search_terms) or query.normalized_query)  # 缺少拆分词时退回已校验查询，并仅在来源请求边界规范化兼容字符与分隔标点。
    params: dict[str, str | int] = {  # 初始化统一搜索所需的来源参数。
        "search": search_text,  # 使用 OpenAlex 全文检索承载统一意图。
        "per_page": query.source_recall_count or query.target_paper_count,  # 自然入口扩大召回，旧调用继续兼容最终数量。
        "select": ",".join(OPENALEX_WORK_FIELDS),  # 仅请求映射统一模型所需的最小字段。
    }
    if query.year_range:  # 用户明确给出年份范围时才添加来源过滤条件。
        params["filter"] = f"publication_year:{query.year_range[0]}-{query.year_range[1]}"  # 使用 OpenAlex 支持的发表年份范围语法。
    return params  # 不将来源无法可靠表达的排除词伪装成服务端过滤。


def normalize_openalex_search_text(search_text: str) -> str:
    """将 OpenAlex 搜索文本限定为不含易触发解析歧义的兼容标点。

    该函数只在 OpenAlex 适配器构造请求时调用，不回写 QueryIntent、金标或评测输入。

    参数：
        search_text：由 QuerySchema 或 QueryIntent 汇总得到的原始来源检索文本。
    返回：
        str：已统一兼容字符、删除撇号、替换分隔标点并压缩空白的搜索文本。
    异常：
        ValueError：规范化后不含任何可搜索文本时抛出。
    """
    compatibility_text = unicodedata.normalize("NFKC", search_text)  # 统一全角问号等兼容字符，但不翻译或改写词语。
    without_apostrophes = compatibility_text.translate(_OPENALEX_SEARCH_APOSTROPHE_TRANSLATION)  # 删除所有格撇号以保留相邻词干。
    separated_text = _OPENALEX_SEARCH_SEPARATOR_PATTERN.sub(" ", without_apostrophes)  # 将问号和引号等查询分隔符安全转换为空格。
    normalized_text = " ".join(separated_text.split())  # 压缩连续空白，构造稳定的来源请求文本。
    if not normalized_text:  # 纯标点输入不能形成安全且可解释的来源搜索请求。
        raise ValueError("OpenAlex 搜索文本在来源规范化后为空")  # 阻止向供应商发送无约束或无意义的请求。
    return normalized_text  # 返回仅供当前来源请求使用的确定性文本。


class OpenAlexClient(AcademicSearchAdapter):
    """封装 OpenAlex /works 请求、响应校验和论文映射。

    参数：
        settings_override：测试或多环境场景下可替换的配置对象。
        transport：可选 HTTP 传输层，仅用于无网络单元测试或定制网络策略。
    """

    def __init__(
        self,
        settings_override: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        response_cache: SourceResponseCache | None = None,
        source_rate_limiter: SourceRateLimiter | None = None,
        request_executor: AcademicApiRequestExecutor | None = None,
    ) -> None:
        """保存客户端配置与可选的 HTTP 传输层。"""
        self._settings = settings_override or settings  # 默认复用经过环境变量校验的全局配置。
        self._transport = transport  # 保留可由测试替换的 HTTP 传输层。
        self._response_cache = response_cache or get_source_response_cache()  # 默认复用应用 Redis 缓存，测试可注入离线替身。
        self._request_executor = request_executor or AcademicApiRequestExecutor("openalex", self._settings, self._settings.openalex_requests_per_second, source_rate_limiter=source_rate_limiter)  # 将所有真实 GET 统一交给共享策略。

    source = "openalex"  # 声明统一适配器协议要求的稳定来源名称。

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
        results = await self._request_works(build_openalex_work_params(query))  # 复用安全请求边界并保留旧模型输出兼容性。
        papers: list[Paper] = []  # 保存成功映射的旧版统一论文模型。
        skipped_count = 0  # 统计字段不完整而被跳过的单条 Work 数。
        for result in results:  # 按 OpenAlex 返回顺序处理 Work 记录。
            work = _as_mapping(result)  # 确认单条结果具有对象结构。
            if work is None:  # 非对象结果无法映射为论文。
                skipped_count += 1  # 记录异常条目数量。
                continue  # 继续处理其余结果。
            try:  # 单条 Work 失败不应丢弃整页有效结果。
                papers.append(map_openalex_work_to_paper(work))  # 映射并保存旧入口所需的基础论文。
            except OpenAlexMappingError:  # 仅跳过缺失必要字段的 Work。
                skipped_count += 1  # 记录映射失败数量。
        logger.info("OpenAlex 兼容检索完成：原始结果=%d，映射成功=%d，跳过=%d", len(results), len(papers), skipped_count)  # 记录旧入口检索统计。
        return papers  # 返回旧服务层可继续消费的基础论文模型。

    async def search(self, query: QueryIntent) -> list[PaperRecord]:
        """以统一 QueryIntent 搜索 OpenAlex 并返回可溯源的 PaperRecord。

        参数：
            query：由查询规划节点输出的统一检索意图。
        返回：
            list[PaperRecord]：保留 OpenAlex 原始排名和来源标识的论文记录。
        异常：
            OpenAlexClientError：来源配置、网络、HTTP 或响应结构异常时抛出。
        """
        results = await self._request_works(build_openalex_search_params(query))  # 使用统一意图构造来源请求并获取原始结果。
        papers: list[PaperRecord] = []  # 保存成功映射的多源融合输入。
        skipped_count = 0  # 统计无法构造稳定论文记录的来源条目。
        for raw_rank, result in enumerate(results, start=1):  # 保留来源返回顺序供后续 RRF 融合使用。
            work = _as_mapping(result)  # 确认单条结果具有对象结构。
            if work is None:  # 非对象条目无法映射为统一论文。
                skipped_count += 1  # 累加结构异常条目数量。
                continue  # 继续处理同页其余结果。
            try:  # 单条映射失败不能影响同页其他有效论文。
                papers.append(map_openalex_work_to_record(work, raw_rank=raw_rank))  # 映射并写入来源溯源与排名。
            except OpenAlexMappingError:  # 仅跳过缺少主标识或标题的无效 Work。
                skipped_count += 1  # 累加映射失败数量。
        logger.info("OpenAlex 统一检索完成：原始结果=%d，映射成功=%d，跳过=%d", len(results), len(papers), skipped_count)  # 记录不含完整查询的阶段统计。
        return papers  # 返回可直接进入多源融合的论文记录。

    async def _request_works(self, params: dict[str, str | int]) -> list[object]:
        """执行一次已构造参数的 OpenAlex 请求并校验顶层结果数组。

        参数：
            params：不含密钥的 OpenAlex /works 请求参数。
        返回：
            list[object]：未经单条映射的来源原始结果数组。
        异常：
            OpenAlexClientError：配置、网络、状态码或响应结构异常时抛出。
        """
        request_params = dict(params)  # 复制调用方参数，避免注入密钥时修改其可测试输入。
        try:  # 将缺失的部署配置转换为适配层领域错误。
            request_params["api_key"] = self._settings.require_openalex_api_key()  # 在真正请求前注入并校验密钥。
        except ValueError:  # 不将环境变量内容或配置实现细节暴露给上层。
            logger.error("OpenAlex 服务未配置 API 密钥")  # 记录安全的部署错误信息。
            raise OpenAlexClientError("OpenAlex 服务尚未配置") from None  # 返回稳定且不含密钥的领域错误。
        cache_key = self._response_cache.build_key("openalex", "works", params)  # 仅使用不含密钥的规范化参数构造缓存键。
        cached_results = await self._response_cache.get_list(cache_key, "openalex", "works")  # Redis 不可用或未命中时自动回退为空值。
        if cached_results is not None:  # 缓存命中时不消耗 OpenAlex 请求配额。
            return cached_results  # 返回与网络成功路径一致的原始结果数组。
        try:  # 捕获 HTTP 层异常并转换为不会暴露 URL 参数的领域错误。
            async with httpx.AsyncClient(  # 为单次请求创建可自动关闭的异步客户端。
                base_url=self._settings.openalex_api_base_url,  # 使用集中配置的 OpenAlex 地址。
                timeout=self._settings.openalex_timeout_seconds,  # 使用集中配置的请求超时。
                transport=self._transport,  # 在测试时使用本地 MockTransport。
            ) as client:
                response = await self._request_executor.execute(lambda: client.get("/works", params=request_params))  # 每次重试均重新经过来源 RPS 与 Redis 窗口。
                response.raise_for_status()  # 将非成功 HTTP 状态转换为异常。
                payload = response.json()  # 解码 JSON 响应供后续结构校验。
        except httpx.HTTPStatusError as error:  # 单独记录安全的状态码而不记录含密钥 URL。
            error_message, error_category = _map_openalex_http_error(error.response.status_code, error.response)  # 仅从供应商正文提取白名单参数名，绝不回显正文。
            parameter_names = _safe_openalex_request_parameter_names(request_params)  # 仅保留真实发送的参数名称，排除所有参数值。
            logger.error("OpenAlex 请求失败，状态码=%d，错误类别=%s，实际参数=%s", error.response.status_code, error_category, parameter_names)  # 输出可观测但不含查询、密钥和响应正文的分类信息。
            raise OpenAlexClientError(error_message) from None  # 隐藏原始请求 URL、参数和供应商响应体。
        except httpx.RequestError as error:  # 捕获超时、连接和传输错误。
            logger.error("OpenAlex 网络请求失败，错误类型=%s", type(error).__name__)  # 仅记录安全的异常类型。
            raise OpenAlexClientError("OpenAlex 网络请求失败") from None  # 避免异常链泄露请求参数。
        except AcademicApiNetworkError:  # 统一执行器耗尽临时网络重试后保留既有领域错误边界。
            raise OpenAlexClientError("OpenAlex 网络请求失败") from None  # 不向协调器泄露传输层细节。
        except SourceCooldownError:  # 本地或 Redis 冷却期内不得继续访问供应商。
            raise OpenAlexClientError("OpenAlex 请求受限，当前处于冷却期") from None  # 保持单来源可降级语义。
        except ValueError:  # 保留 JSON 解码错误给下方统一响应结构处理。
            logger.error("OpenAlex 响应不是有效 JSON")  # 记录不包含响应正文的解析错误。
            raise OpenAlexClientError("OpenAlex 响应格式无效") from None  # 返回稳定的领域错误。

        response_data = _as_mapping(payload)  # 确认响应根对象是 JSON 映射。
        results = response_data.get("results") if response_data else None  # 读取 OpenAlex 列表响应的结果数组。
        if not isinstance(results, list):  # 缺少结果数组代表 API 响应结构与预期不符。
            logger.error("OpenAlex 响应缺少 results 数组")  # 记录可定位的结构异常。
            raise OpenAlexClientError("OpenAlex 响应缺少 results 数组")  # 阻止错误数据进入后续排序流程。
        await self._response_cache.set_list(cache_key, "openalex", "works", results)  # 仅缓存通过顶层结构校验的成功响应。
        return results  # 将单条映射和模型选择留给兼容入口或统一入口处理。


def _map_openalex_http_error(status_code: int, response: httpx.Response | None = None) -> tuple[str, str]:
    """将 OpenAlex HTTP 状态码转换为不含供应商正文的稳定诊断。

    参数：
        status_code：由 HTTP 客户端提供的响应状态码。
        response：可选响应对象；仅在 400 时读取 JSON 顶层文本以提取安全参数名。
    返回：
        tuple[str, str]：面向调用方的安全错误文本与仅供日志使用的错误类别。
    """
    if status_code == 400:  # 400 表示请求形状或参数不被来源接受，不能安全回显响应正文。
        parameter_hint = _extract_openalex_safe_parameter_hint(response)  # 从白名单中提取可安全展示的参数名。
        if parameter_hint is not None:  # 仅在供应商错误文本明确提到已知参数时增加定位信息。
            return f"OpenAlex 请求参数无效（HTTP 400，供应商提示={parameter_hint}）", f"invalid_request:{parameter_hint}"  # 明确该名称来自供应商提示，不将其误表述为已确认的实际参数。
        return "OpenAlex 请求参数无效（HTTP 400）", "invalid_request"  # 无可靠安全提示时维持既有泛化错误。
    if status_code in {401, 403}:  # 认证或授权问题需要用户在本机检查配置，但不得打印密钥。
        return f"OpenAlex 认证或访问权限无效（HTTP {status_code}）", "authentication_or_authorization"  # 合并同类部署错误以避免泄露供应商细节。
    if status_code == 404:  # /works 等固定端点不存在通常代表 API 基地址或端点配置错误。
        return "OpenAlex 服务端点不存在（HTTP 404）", "endpoint_not_found"  # 提供可操作的配置排查类别。
    if status_code == 429:  # 最终限流仍应保留来源受限语义，而非伪装成参数错误。
        return "OpenAlex 请求受限（HTTP 429）", "rate_limited"  # 共享执行器已负责重试与冷却。
    return f"OpenAlex 请求失败（HTTP {status_code}）", "http_error"  # 其他状态仅保留安全状态码。


def _extract_openalex_safe_parameter_hint(response: httpx.Response | None) -> str | None:
    """从 OpenAlex 400 JSON 中提取白名单参数名，不保留或输出原始错误正文。

    参数：
        response：可选 OpenAlex HTTP 响应对象。
    返回：
        str | None：预定义安全参数名；响应非 JSON、结构异常或未命中时返回空值。
    """
    if response is None:  # 没有响应对象时无法解析供应商诊断。
        return None  # 保持调用方的安全泛化错误。
    try:  # 供应商错误通常为 JSON，但不能假设所有代理错误都满足该结构。
        response_data = _as_mapping(response.json())  # 只接受顶层对象，拒绝数组和其他 JSON 类型。
    except ValueError:  # 非 JSON 或损坏 JSON 不应覆盖原始 HTTP 错误处理。
        return None  # 不记录解析异常或原始正文。
    if response_data is None:  # 顶层不是 JSON 对象时没有可信字段可读取。
        return None  # 仅保留泛化错误。
    diagnostic_texts = tuple(value.casefold() for field_name in ("error", "message") if isinstance((value := response_data.get(field_name)), str))  # 仅暂存供应商标准诊断字段用于内存匹配。
    if not diagnostic_texts:  # 缺少文本诊断字段时不能猜测参数名。
        return None  # 保持泛化错误而不暴露任何其他响应字段。
    diagnostic_text = "\n".join(diagnostic_texts)  # 仅在函数局部组合文本，后续不会记录或返回该变量。
    for parameter_name, aliases in _OPENALEX_SAFE_ERROR_PARAMETER_ALIASES:  # 按稳定白名单顺序检查已知参数。
        if any(_contains_openalex_parameter_alias(diagnostic_text, alias) for alias in aliases):  # 命中只说明供应商文本提到了安全参数名。
            return parameter_name  # 返回固定白名单名称而非供应商原文。
    return None  # 未命中时拒绝猜测并继续隐藏供应商诊断正文。


def _safe_openalex_request_parameter_names(params: Mapping[str, object]) -> str:
    """返回实际请求参数的稳定名称列表，不暴露任何参数值。

    参数：
        params：已经注入认证信息、即将发送给 HTTP 客户端的参数映射。
    返回：
        str：按字典序排列、以逗号分隔的参数名称。
    """
    return ",".join(sorted(str(parameter_name) for parameter_name in params))  # 参数名可定位配置污染，排序保证日志和离线断言稳定。


def _contains_openalex_parameter_alias(diagnostic_text: str, alias: str) -> bool:
    """判断供应商诊断文本是否包含完整的预定义参数别名。

    参数：
        diagnostic_text：只在当前调用栈内存中存在的已小写化错误文本。
        alias：预定义的安全参数别名。
    返回：
        bool：是否以完整词边界命中该别名。
    """
    alias_pattern = re.escape(alias.casefold())  # 转义白名单别名，避免参数名中的下划线等字符成为正则语法。
    return re.search(rf"(?<![a-z0-9_]){alias_pattern}(?![a-z0-9_])", diagnostic_text) is not None  # 只接受完整参数名，避免把普通单词片段误判为参数。


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
        author_id = _optional_text(author_data.get("id"))  # 提取可选的 OpenAlex 作者来源标识。
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
                source_author_ids={"openalex": author_id} if author_id else {},  # 仅在来源给出标识时保留平台作者 ID。
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


def map_openalex_work_to_record(work: Mapping[str, object], raw_rank: int | None = None) -> PaperRecord:
    """将一条 OpenAlex Work 响应映射为带来源溯源的 PaperRecord。

    参数：
        work：已经由 HTTP 客户端解码的单条 OpenAlex Work JSON 对象。
        raw_rank：该论文在当前来源搜索结果中的一开始排名。
    返回：
        PaperRecord：可直接用于多源融合、去重和排序的规范化论文记录。
    异常：
        OpenAlexMappingError：Work 缺少有效 id 或 title 时由基础映射器抛出。
    """
    paper = map_openalex_work_to_paper(work)  # 复用已覆盖的基础字段映射，避免两套字段规则漂移。
    return PaperRecord(  # 扩展基础论文为多源融合所需的可溯源记录。
        **paper.model_dump(),  # 保留基础论文模型已经校验的所有兼容字段。
        openalex_id=paper.paper_id,  # 显式保存 OpenAlex 稳定 Work 标识。
        source_records=[PaperSourceRecord(source="openalex", external_id=paper.paper_id, raw_rank=raw_rank)],  # 写入来源与原始排名供融合解释使用。
    )
