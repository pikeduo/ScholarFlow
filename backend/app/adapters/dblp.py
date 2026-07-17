"""封装 DBLP 出版物搜索、来源级节流与统一论文映射。"""

import html  # 解码 DBLP 标题中可能出现的 HTML 实体。
import re  # 清理 DBLP 标题中可能出现的少量展示标签。
from collections.abc import Mapping  # 安全读取嵌套 JSON 对象。

import httpx  # 提供异步 HTTP 客户端和可注入测试传输层。

from backend.app.adapters.base import AcademicSearchAdapter  # 实现 LangGraph 可替换的统一适配器协议。
from backend.app.adapters.academic_api import AcademicApiNetworkError, AcademicApiRequestExecutor  # 复用统一的幂等请求重试、RPS 与冷却边界。
from backend.app.core.config import Settings, settings  # 读取 DBLP 地址、超时和来源级限流配置。
from backend.app.core.logging import logger  # 记录不含完整查询的来源调用统计与错误。
from backend.app.models.paper import PaperAuthor, PaperRecord, PaperSourceRecord  # 构造保留来源溯源信息的统一论文记录。
from backend.app.models.query_intent import QueryIntent  # 接收查询规划节点输出的统一意图。
from backend.app.repositories.source_rate_limiter import SourceCooldownError, SourceRateLimiter  # 将共享冷却状态转换为来源领域异常。


HTML_TAG_PATTERN = re.compile(r"<[^>]+>")  # 匹配仅用于展示且不应进入统一标题字段的 HTML 标签。


class DblpMappingError(ValueError):
    """表示 DBLP 响应缺少生成统一论文所必需的数据。"""


class DblpClientError(RuntimeError):
    """表示 DBLP HTTP 调用或响应结构不可用。"""


def build_dblp_search_params(query: QueryIntent) -> dict[str, str | int]:
    """将 QueryIntent 转换为 DBLP 出版物搜索的单页请求参数。

    参数：
        query：已由查询规划节点校验的统一检索意图。
    返回：
        dict[str, str | int]：不含密钥、可直接用于 DBLP `/api` 端点的请求参数。
    """
    search_text = select_dblp_primary_topic(query)  # DBLP 空格语义等价 AND，因此只选择一个宽泛主要研究主题。
    return {  # 返回 DBLP 官方出版物搜索 API 所需的单页参数。
        "q": search_text,  # 使用来源默认的出版物关键词搜索语义。
        "format": "json",  # 请求方便安全映射的官方 JSON 响应格式。
        "h": query.source_recall_count or query.target_paper_count,  # 使用独立来源召回规模并兼容旧调用。
        "f": 0,  # 首版仅请求每次搜索结果的第一页。
        "c": 0,  # 后端不消费自动补全，关闭其计算以降低泛词在线检索的来源负担与 5xx 风险。
    }


def select_dblp_primary_topic(query: QueryIntent) -> str:
    """从研究主题中选择单个宽泛 DBLP 查询，主题为空时回退规范化查询。

    参数：
        query：完整 QueryIntent；本函数仅读取 research_topics 与必要的 normalized_query 回退。
    返回：
        str：适合 DBLP 单页召回的一个宽泛主题文本。
    """
    topics = [_normalize_search_term(topic) for topic in query.research_topics if _normalize_search_term(topic)]  # 仅保留实际可检索的研究主题，忽略方法、任务和约束字段。
    if not topics:  # 结构化主题完全缺失时才允许使用规范化查询保持来源可调用。
        return _normalize_search_term(query.normalized_query)  # 不改变原 QueryIntent，也不拼入年份或其他条件。
    combined_time_series_forecasting = _combine_time_series_forecasting_topic(topics)  # 优先构造常见且更宽泛的时间序列预测主主题。
    if combined_time_series_forecasting is not None:  # 只有研究主题本身同时提供两个明确语义时才使用该保守组合。
        return combined_time_series_forecasting  # 避免将模型、任务或硬约束拼入 DBLP q。
    return min(enumerate(topics), key=lambda indexed_topic: _dblp_topic_priority(indexed_topic[1], indexed_topic[0]))[1]  # 优先选择含实际任务词、较短且更早出现的主题。


def _combine_time_series_forecasting_topic(topics: list[str]) -> str | None:
    """从多个研究主题中保守识别可合并为 time series forecasting 的宽泛主任务。"""
    has_time_series = any("time series" in topic.casefold() for topic in topics)  # 只基于研究主题判断是否存在时间序列研究对象。
    has_forecasting = any("forecast" in topic.casefold() for topic in topics)  # 只基于研究主题判断是否存在预测任务。
    return "time series forecasting" if has_time_series and has_forecasting else None  # 两项同时存在时生成常见宽泛检索主题，否则不作推断。


def _dblp_topic_priority(topic: str, index: int) -> tuple[int, int, int]:
    """为 DBLP 单主题召回计算稳定的宽泛性优先级。"""
    has_task_word = int(not any(keyword in topic.casefold() for keyword in ("forecast", "prediction", "classification", "retrieval", "generation")))  # 优先保留含明确研究任务的主题而不是纯模型名称。
    return has_task_word, len(topic.split()), index  # 任务主题优先，其次选择更短表达，最后保持输入顺序。


class DblpClient(AcademicSearchAdapter):
    """实现 DBLP 单页出版物搜索、JSON 校验和来源级节流。

    参数：
        settings_override：测试或多环境场景下可替换的配置对象。
        transport：可选 HTTP 传输层，仅用于无网络单元测试或定制网络策略。
    """

    source = "dblp"  # 声明当前客户端实现的统一来源名称。

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
        self._request_executor = request_executor or AcademicApiRequestExecutor("dblp", self._settings, self._settings.dblp_requests_per_second, source_rate_limiter=source_rate_limiter)  # 统一管理来源 RPS、重试和冷却。

    async def search(self, query: QueryIntent) -> list[PaperRecord]:
        """搜索 DBLP 出版物并返回保留来源排名的统一论文记录。

        参数：
            query：包含检索词和目标数量的完整查询意图。
        返回：
            list[PaperRecord]：已映射且保留 DBLP 来源溯源信息的论文列表。
        异常：
            DblpClientError：HTTP、网络、JSON 或响应结构异常时抛出。
        """
        params = build_dblp_search_params(query)  # 构造不含用户密钥的可测试请求参数。
        has_research_topics = any(_normalize_search_term(topic) for topic in query.research_topics)  # 仅记录本次是否使用规范化查询回退，不记录主题正文。
        try:  # 将 HTTP 层异常转换为不泄露响应正文的领域错误。
            async with httpx.AsyncClient(  # 为单次请求创建可自动关闭的异步客户端。
                base_url=self._settings.dblp_api_base_url,  # 使用集中配置的 DBLP 出版物搜索地址。
                timeout=self._settings.dblp_timeout_seconds,  # 使用集中配置的请求超时。
                transport=self._transport,  # 在测试时使用本地 MockTransport。
            ) as client:
                response = await self._request_executor.execute(lambda: client.get("/api", params=params))  # 每次重试均重新通过统一来源限流。
                response.raise_for_status()  # 将非成功 HTTP 状态转换为可统一处理的异常。
                payload = response.json()  # 解码 JSON 响应供结构校验与映射使用。
        except httpx.HTTPStatusError as error:  # 单独记录不含响应正文的 HTTP 状态码。
            logger.error("DBLP 请求失败，状态码=%d", error.response.status_code)  # 输出安全且可观测的来源错误。
            raise DblpClientError(f"DBLP 请求失败（HTTP {error.response.status_code}）") from None  # 隐藏底层请求上下文。
        except httpx.RequestError as error:  # 捕获连接、超时和传输失败。
            logger.error("DBLP 网络请求失败，错误类型=%s", type(error).__name__)  # 仅记录安全的异常类型。
            raise DblpClientError("DBLP 网络请求失败") from None  # 返回稳定的领域错误。
        except AcademicApiNetworkError:  # 统一执行器耗尽网络重试后维持来源异常契约。
            raise DblpClientError("DBLP 网络请求失败") from None  # 不泄露传输层细节。
        except SourceCooldownError:  # 本地或 Redis 冷却期间直接降级。
            raise DblpClientError("DBLP 请求受限，当前处于冷却期") from None  # 不影响其他来源。
        except ValueError:  # 捕获无效 JSON 等解析失败。
            logger.error("DBLP 响应不是有效 JSON")  # 不记录可能过大的原始响应正文。
            raise DblpClientError("DBLP 响应格式无效") from None  # 返回不泄露内部细节的稳定错误。

        hits = _extract_dblp_hits(payload)  # 校验并读取官方 JSON 中的命中数组。
        papers: list[PaperRecord] = []  # 保存成功映射的统一多源论文记录。
        skipped_count = 0  # 统计字段不完整而无法映射的单条命中数量。
        for raw_rank, hit in enumerate(hits, start=1):  # 保留来源返回顺序作为 RRF 所需的原始排名。
            try:  # 单条映射失败不应丢弃整页可用结果。
                papers.append(map_dblp_hit(hit, raw_rank=raw_rank))  # 映射并保留来源原始排名。
            except DblpMappingError:  # 仅跳过缺少必要标识或标题的命中。
                skipped_count += 1  # 累加映射失败统计。
        returned_papers = _filter_dblp_year_range(papers, query)  # DBLP 不在 q 中拼年份，映射后再按用户年份硬约束本地过滤。
        logger.info("来源检索完成：来源=dblp，主题数=%d，查询词数=%d，使用年份=%s，回退规范化查询=%s，返回数量=%d，原始结果=%d，映射成功=%d，跳过=%d", len([topic for topic in query.research_topics if _normalize_search_term(topic)]), len(params["q"].split()), query.year_range is not None, not has_research_topics, len(returned_papers), len(hits), len(papers), skipped_count)  # 只记录安全统计，不记录完整用户问题或来源查询字符串。
        return returned_papers  # 返回已完成来源级年份过滤的统一论文记录，后续仍执行全部规则与核验。


def _filter_dblp_year_range(papers: list[PaperRecord], query: QueryIntent) -> list[PaperRecord]:
    """在 DBLP 映射后应用可验证的本地年份闭区间过滤。"""
    if query.year_range is None:  # 未指定年份时不能额外过滤来源返回结果。
        return papers  # 保持 DBLP 原始映射顺序与完整候选集合。
    start_year, end_year = query.year_range  # 解构 QueryIntent 已校验的年份闭区间。
    return [paper for paper in papers if paper.year is not None and start_year <= paper.year <= end_year]  # 年份未知或不在范围内的来源记录交由此处明确移除。


def map_dblp_hit(hit: Mapping[str, object], raw_rank: int | None = None) -> PaperRecord:
    """将一条 DBLP 出版物命中映射为可溯源的 PaperRecord。

    参数：
        hit：已经由 HTTP 客户端解码的单条 DBLP 命中对象。
        raw_rank：该论文在当前来源搜索结果中的一开始排名。
    返回：
        PaperRecord：可进入多源融合和后续排序的规范化论文记录。
    异常：
        DblpMappingError：缺少有效 key 或 title 时抛出。
    """
    info = _as_mapping(hit.get("info"))  # 读取 DBLP 命中内的出版物元数据对象。
    if info is None:  # 没有 info 对象时无法获取稳定键与标题。
        raise DblpMappingError("DBLP 命中缺少有效字段：info")  # 返回可定位且不含原始响应的映射错误。
    dblp_key = _required_text(info, "key")  # 使用 DBLP 出版物键构造来源内稳定标识。
    return PaperRecord(  # 构造并交由 Pydantic 二次校验的多源论文记录。
        paper_id=f"dblp:{dblp_key}",  # 使用带来源前缀的稳定标识避免跨来源主键冲突。
        title=_required_text(info, "title"),  # 使用 DBLP 出版物标题字段。
        abstract="",  # DBLP 搜索 API 不提供摘要，保持空字符串而不虚构内容。
        authors=_extract_authors(info),  # 规范化作者显示名称。
        year=_extract_year(info.get("year")),  # 读取可选出版年份。
        venue=_first_text(info, ("venue", "journal", "booktitle")),  # 兼容会议、期刊与书籍条目的出版载体字段。
        doi=_optional_text(info.get("doi")),  # 保留来源提供的可选 DOI。
        citation_count=0,  # DBLP 搜索 API 不提供引用次数，不能虚构质量信号。
        references=[],  # DBLP 搜索 API 不提供真实引用关系，保持为空列表。
        source="dblp",  # 标记当前统一记录的主来源。
        paper_type=_map_paper_type(_optional_text(info.get("type"))),  # 将 DBLP 出版物类型映射为首版支持类型。
        dblp_key=dblp_key,  # 显式保留 DBLP 出版物键供跨来源融合使用。
        source_records=[PaperSourceRecord(source="dblp", external_id=dblp_key, raw_rank=raw_rank)],  # 写入来源与原始排名供融合解释使用。
    )


def _extract_dblp_hits(payload: object) -> list[Mapping[str, object]]:
    """从 DBLP 搜索响应中校验并提取可映射的命中对象列表。"""
    response_data = _as_mapping(payload)  # 确认 JSON 根对象是映射。
    result_data = _as_mapping(response_data.get("result")) if response_data else None  # 读取 DBLP 官方 result 对象。
    hits_data = _as_mapping(result_data.get("hits")) if result_data else None  # 读取 result 下的 hits 对象。
    if hits_data is None:  # 缺少官方 result.hits 对象表示响应结构与端点契约不符。
        logger.error("DBLP 响应缺少 result.hits 对象")  # 记录可定位结构异常但不输出响应正文。
        raise DblpClientError("DBLP 响应缺少有效命中列表")  # 阻止无关 JSON 被误判为空结果。
    raw_hits = hits_data.get("hit")  # 读取可能为对象或数组的命中字段。
    if raw_hits is None:  # DBLP 合法空结果可能只提供命中总数而不提供 hit 字段。
        return []  # 将合法空结果转换为统一空列表。
    if isinstance(raw_hits, Mapping):  # DBLP 单条命中时会将 hit 压缩为对象。
        return [raw_hits]  # 统一包装为列表以简化调用方处理。
    if isinstance(raw_hits, list):  # DBLP 多条命中时返回对象数组。
        return [hit for hit in raw_hits if isinstance(hit, Mapping)]  # 跳过结构异常条目并保留可映射对象。
    logger.error("DBLP 响应中的 hit 字段类型无效")  # 记录来源结构变化但不输出响应正文。
    raise DblpClientError("DBLP 响应缺少有效命中列表")  # 阻止错误数据进入融合流程。


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将 JSON 值安全转换为可读取的字符串键映射。"""
    return value if isinstance(value, Mapping) else None  # 拒绝列表、字符串和空值等非对象字段。


def _normalize_search_term(value: str) -> str:
    """压缩 QueryIntent 词语空白，避免向 DBLP 发送空检索项。"""
    return " ".join(value.split())  # 保留用户文本语义，不插入供应商专用查询语法。


def _optional_text(value: object) -> str | None:
    """提取、去标签并解码后的可选 DBLP 文本字段。"""
    if not isinstance(value, str):  # 非字符串不能作为统一模型文本字段。
        return None  # 以空值表示字段缺失或类型异常。
    normalized_text = " ".join(HTML_TAG_PATTERN.sub(" ", html.unescape(value)).split())  # 解码实体、移除展示标签并压缩空白。
    return normalized_text or None  # 将空字符串统一视为缺失。


def _required_text(data: Mapping[str, object], field_name: str) -> str:
    """读取 DBLP 元数据对象中的必要文本字段。"""
    text_value = _optional_text(data.get(field_name))  # 读取并规范化必要字段。
    if text_value is None:  # 稳定来源键或可展示标题缺失时无法构造论文记录。
        raise DblpMappingError(f"DBLP 命中缺少有效字段：{field_name}")  # 返回可定位的映射错误。
    return text_value  # 返回已经通过空值校验的文本。


def _extract_authors(info: Mapping[str, object]) -> list[PaperAuthor]:
    """提取 DBLP 出版物作者名称，并兼容单作者对象和多作者数组。"""
    authors_data = _as_mapping(info.get("authors"))  # 读取可选作者包装对象。
    raw_authors = authors_data.get("author") if authors_data else None  # 读取可能为字符串、对象或数组的作者字段。
    candidates = raw_authors if isinstance(raw_authors, list) else [raw_authors]  # 统一单作者与多作者为可迭代列表。
    authors: list[PaperAuthor] = []  # 累积可构造的统一作者模型。
    for candidate in candidates:  # 按来源返回顺序处理每位作者。
        author_name = _optional_text(candidate)  # 优先支持 DBLP 常见的作者字符串形式。
        if author_name is None and isinstance(candidate, Mapping):  # 兼容未来或变体响应中的作者对象。
            author_name = _optional_text(candidate.get("text")) or _optional_text(candidate.get("#text"))  # 读取常见文本承载字段。
        if author_name is not None:  # 作者模型要求显示名称有效。
            authors.append(PaperAuthor(name=author_name))  # DBLP 搜索响应未提供稳定作者 ID 时仅保留名称。
    return authors  # 返回保持来源顺序的可用作者列表。


def _extract_year(value: object) -> int | None:
    """从 DBLP 可选年份字段提取合理的四位出版年份。"""
    year_text = _optional_text(value)  # 读取并规范化可能为字符串的年份字段。
    return int(year_text) if year_text and year_text.isdigit() else None  # 非纯数字年份保持未知而不阻断检索。


def _first_text(data: Mapping[str, object], field_names: tuple[str, ...]) -> str | None:
    """按给定优先级读取 DBLP 元数据中的第一个可用文本字段。"""
    for field_name in field_names:  # 依次检查不同出版载体字段。
        value = _optional_text(data.get(field_name))  # 读取当前字段的规范化文本。
        if value is not None:  # 找到有效文本时立即停止后续回退。
            return value  # 返回最高优先级可用出版载体。
    return None  # 全部字段缺失时保持出版载体未知。


def _map_paper_type(type_name: str | None) -> str | None:
    """将 DBLP 出版物类型映射为首版支持的基础论文类型。"""
    normalized_type = type_name.casefold() if type_name else ""  # 规范化可选类型以进行不区分大小写匹配。
    if "conference" in normalized_type or "workshop" in normalized_type:  # 优先识别会议和研讨会论文。
        return "conference"  # 返回统一会议论文类型。
    if "journal" in normalized_type or "article" in normalized_type:  # 识别期刊文章类型。
        return "article"  # 返回统一文章论文类型。
    return None  # 其余 DBLP 类型暂不强制映射为不准确的论文类型。
