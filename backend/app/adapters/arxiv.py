"""封装 arXiv Atom 搜索、来源级节流与统一论文映射。"""

import re  # 规范化 arXiv 标识中的可选版本号。
import xml.etree.ElementTree as ElementTree  # 使用标准库解析 arXiv 返回的 Atom XML。
from urllib.parse import unquote, urlparse  # 从 arXiv 抽象页 URL 提取稳定论文标识。

import httpx  # 提供异步 HTTP 客户端和可注入测试传输层。

from backend.app.adapters.base import AcademicSearchAdapter  # 实现 LangGraph 可替换的统一适配器协议。
from backend.app.adapters.academic_api import AcademicApiNetworkError, AcademicApiRequestExecutor  # 复用统一的幂等请求重试、RPS 与冷却边界。
from backend.app.core.config import Settings, settings  # 读取 arXiv 地址、超时和来源级限流配置。
from backend.app.core.logging import logger  # 记录不含完整查询的来源调用统计与错误。
from backend.app.models.paper import PaperAuthor, PaperRecord, PaperSourceRecord  # 构造保留来源溯源信息的统一论文记录。
from backend.app.models.query_intent import QueryIntent  # 接收查询规划节点输出的统一意图。
from backend.app.repositories.source_rate_limiter import SourceCooldownError, SourceRateLimiter  # 将共享冷却状态转换为来源领域异常。


ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"  # 声明 arXiv Atom 1.0 元素命名空间。
ARXIV_NAMESPACE = "http://arxiv.org/schemas/atom"  # 声明 arXiv 扩展元数据命名空间。
ARXIV_IDENTIFIER_VERSION_PATTERN = re.compile(r"v\d+$")  # 匹配现代与旧式 arXiv 标识末尾的版本号。
_ARXIV_MAX_CONCEPT_COUNT = 2  # 限制前置召回仅使用两个核心概念组，避免过度收窄。
_ARXIV_MAX_VARIANT_COUNT = 4  # 限制单个概念组中的别名数量，保持请求可审计。
_ARXIV_LONG_TERM_WORD_LIMIT = 6  # 超过该词数的自然语言表达不得整体作为精确短语。
_ARXIV_GENERIC_WORDS = frozenset({"recent", "latest", "paper", "papers", "study", "studies", "research", "researches"})  # 集中管理少量不应单独构成检索概念的通用修饰词。
_ARXIV_CONNECTOR_PATTERN = re.compile(r"\b(?:for|using|with|on|in|about|regarding)\b", flags=re.IGNORECASE)  # 识别长子查询中可安全切分的常见英文连接词。
_ARXIV_BOOLEAN_PATTERN = re.compile(r"\b(?:AND|OR|NOT)\b", flags=re.IGNORECASE)  # 移除用户提供的布尔词，防止其被误解为 arXiv 原始语法。
_ARXIV_FIELD_PREFIX_PATTERN = re.compile(r"\b(?:all|ti|abs|au|cat)\s*:", flags=re.IGNORECASE)  # 移除常见 arXiv 字段前缀，确保字段选择始终由编译器控制。
_ARXIV_TERM_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:-[^\W_]+)*", flags=re.UNICODE)  # 提取可安全重组为短语的 Unicode 单词与连字符词。
_ARXIV_ALIASES = {  # 只维护少量高频且语义明确的计算机领域别名。
    "large language model": ("large language model", "large language models", "LLM"),
    "large language models": ("large language model", "large language models", "LLM"),
    "graph neural network": ("graph neural network", "graph neural networks", "GNN"),
    "graph neural networks": ("graph neural network", "graph neural networks", "GNN"),
}  # 别名顺序同时定义生成顺序，避免随机或模型推断。
_ARXIV_HYPHENATED_PHRASES = ("time series", "zero shot", "few shot", "deep learning", "machine learning", "self supervised")  # 仅为常见稳定短语生成可读连字符变体。
_ARXIV_PLURAL_PAIRS = {"model": "models", "models": "model", "network": "networks", "networks": "network", "transformer": "transformers", "transformers": "transformer"}  # 仅为语义明确的名词尾词生成单复数变体。


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
    return {  # 返回官方 Query API 使用的单页参数。
        "search_query": compile_arxiv_search_query(query),  # 只使用 arXiv 专属的有限概念组编译表达。
        "start": 0,  # 首版仅请求每次搜索的第一页。
        "max_results": query.source_recall_count or query.target_paper_count,  # 使用独立来源召回规模并兼容旧调用。
        "sortBy": "relevance",  # 保留来源默认的相关性排序供后续融合使用。
        "sortOrder": "descending",  # 使用官方支持的降序排序取值。
    }


def compile_arxiv_search_query(query: QueryIntent) -> str:
    """将统一意图编译为仅含有限核心概念的安全 arXiv 搜索表达。"""
    clauses = [_build_arxiv_concept_group(concept) for concept in select_arxiv_concepts(query)]  # 为每个已选概念构造独立 OR 组。
    if query.year_range:  # arXiv 只提供投稿日期过滤，因此以此作为发表年份的近似前置过滤。
        start_year, end_year = query.year_range  # 解构已由 QueryIntent 校验的闭区间年份。
        clauses.append(f"submittedDate:[{start_year}01010000 TO {end_year}12312359]")  # 使用官方要求的 GMT 分钟时间范围格式。
    return " AND ".join(clauses)  # 只让不同概念组和年份过滤形成强制 AND 条件。


def select_arxiv_concepts(query: QueryIntent) -> list[str]:
    """按字段优先级选择最多两个互不重复的 arXiv 召回概念。"""
    selected_concepts: list[str] = []  # 保存保持字段与原文出现顺序的最终核心概念。
    for terms in (query.research_topics, query.methods, query.tasks, query.must_include, query.datasets, [query.normalized_query]):  # 按召回语义强度依次评估候选字段并在最后回退规范化查询。
        for term in terms:  # 保持同一字段内由 Query Agent 或用户提供的稳定顺序。
            for candidate in _split_arxiv_candidate(term):  # 长子查询会先被压缩为可审计的短概念。
                _append_arxiv_concept(selected_concepts, candidate)  # 去重、替换更完整重叠概念或追加新概念。
                if len(selected_concepts) >= _ARXIV_MAX_CONCEPT_COUNT:  # 达到前置检索上限后不再引入更细的强制条件。
                    return selected_concepts  # 保持最高优先级概念的确定性选择。
    return selected_concepts  # QueryIntent 的 normalized_query 非空时通常至少可产生一个概念。


def _append_arxiv_concept(selected_concepts: list[str], candidate: str) -> None:
    """将一个有效候选以大小写无关去重和重叠替换规则加入概念列表。"""
    normalized_candidate = _normalize_search_term(candidate)  # 先移除潜在原始语法并压缩空白。
    if not _is_meaningful_arxiv_concept(normalized_candidate):  # 空白、年份或泛化词不能增加强制检索条件。
        return  # 保留已选概念不受无效候选影响。
    candidate_tokens = _arxiv_concept_tokens(normalized_candidate)  # 将候选转换为可比较的大小写无关语义词项。
    for index, selected_concept in enumerate(selected_concepts):  # 逐个比较已有概念以避免重复 AND 条件。
        selected_tokens = _arxiv_concept_tokens(selected_concept)  # 读取已有概念的稳定词项集合。
        if not _arxiv_concepts_overlap(candidate_tokens, selected_tokens):  # 不重叠时继续寻找可能重复项。
            continue  # 当前已有概念与候选可以共存。
        if len(candidate_tokens) > len(selected_tokens):  # 更完整的同义短语优先保留更多语义限定。
            selected_concepts[index] = normalized_candidate  # 原位替换以保持其字段优先级位置。
        return  # 重叠概念只能保留一个，不能额外形成 AND 组。
    selected_concepts.append(normalized_candidate)  # 新概念与已有概念语义独立时才追加。


def _split_arxiv_candidate(value: str) -> list[str]:
    """将普通术语保留为短语，并把长自然语言表达拆成有限概念。"""
    normalized_value = _normalize_search_term(value)  # 先统一空白并移除用户提供的 arXiv 语法片段。
    tokens = _arxiv_term_tokens(normalized_value)  # 以安全词项判断表达长度和通用修饰词。
    if len(tokens) <= _ARXIV_LONG_TERM_WORD_LIMIT and not _ARXIV_CONNECTOR_PATTERN.search(normalized_value):  # 短且不含连接结构的术语可作为一个概念保留。
        return [normalized_value] if normalized_value else []  # 空白值不应产生空概念组。
    segments = [_shorten_arxiv_segment(segment) for segment in _ARXIV_CONNECTOR_PATTERN.split(normalized_value)]  # 按常见连接词切分长子查询并将每段压缩为短语。
    meaningful_segments = [segment for segment in segments if _is_meaningful_arxiv_concept(segment)]  # 丢弃 recent、paper 或年份等没有召回意义的片段。
    if meaningful_segments:  # 可可靠切分时优先保留出现顺序中的短概念。
        return meaningful_segments  # 上层仍会限制最多两个概念组。
    shortened_value = _shorten_arxiv_segment(normalized_value)  # 无可靠连接结构时回退为前几个主要词项。
    return [shortened_value] if _is_meaningful_arxiv_concept(shortened_value) else []  # 绝不将无法拆分的完整长句作为精确短语。


def _shorten_arxiv_segment(value: str) -> str:
    """移除小型通用修饰词集合，并将一个片段限制为六个词以内。"""
    content_tokens = [token for token in _arxiv_term_tokens(value) if token.casefold() not in _ARXIV_GENERIC_WORDS and not _is_arxiv_year_token(token)]  # 只保留可能具有学术检索意义的词项。
    return " ".join(content_tokens[:_ARXIV_LONG_TERM_WORD_LIMIT])  # 保持原词序并防止再次形成超长精确短语。


def _build_arxiv_concept_group(concept: str) -> str:
    """将一个核心概念扩展为最多四项、使用 OR 连接的安全 arXiv 组。"""
    variants = _expand_arxiv_aliases(concept)  # 使用集中且确定的词形或缩写变体。
    clauses = [f'all:"{variant}"' for variant in variants]  # 每个变体均由编译器包裹，用户文本无法注入字段或运算符。
    return clauses[0] if len(clauses) == 1 else f"({' OR '.join(clauses)})"  # 单项不生成无意义括号，多项明确组成 OR 组。


def _expand_arxiv_aliases(concept: str) -> list[str]:
    """返回一个概念的有限同义、连字符和单复数变体。"""
    normalized_concept = _normalize_search_term(concept)  # 确保后续别名扩展只处理安全纯文本。
    variants = list(_ARXIV_ALIASES.get(normalized_concept.casefold(), (normalized_concept,)))  # 已知缩写优先使用集中定义的稳定映射。
    for phrase in _ARXIV_HYPHENATED_PHRASES:  # 为常见词组添加不改变含义的连字符变体。
        if phrase in normalized_concept.casefold():  # 仅在原概念确实包含该词组时扩展。
            variants.append(re.sub(re.escape(phrase), lambda matched_phrase: matched_phrase.group(0).replace(" ", "-"), normalized_concept, flags=re.IGNORECASE))  # 仅替换匹配片段中的空格，保持用户词形的大小写。
    tokens = normalized_concept.split()  # 只检查末尾名词，避免对短语内部词做破坏语义的词干化。
    if tokens and tokens[-1].casefold() in _ARXIV_PLURAL_PAIRS:  # 仅对集中定义的明确名词产生单复数。
        variants.append(" ".join([*tokens[:-1], _preserve_arxiv_token_case(tokens[-1], _ARXIV_PLURAL_PAIRS[tokens[-1].casefold()])]))  # 使用与原末尾词相同大小写的对应词形。
    return _distinct_arxiv_variants(variants)[:_ARXIV_MAX_VARIANT_COUNT]  # 去重后限制数量，避免单组膨胀。


def _preserve_arxiv_token_case(source_token: str, replacement: str) -> str:
    """以原始词项的全大写或首字母大写形式输出词形变体。"""
    if source_token.isupper():  # 缩写或全大写术语不应在变体中丢失展示形式。
        return replacement.upper()  # 保持全大写格式。
    if source_token.istitle():  # 常见模型名或标题式术语应保持首字母大写。
        return replacement.title()  # 保持标题式格式。
    return replacement  # 其他情况使用定义中的小写词形。


def _distinct_arxiv_variants(variants: list[str]) -> list[str]:
    """对词形变体进行大小写无关去重并保持首次出现顺序。"""
    distinct_variants: list[str] = []  # 保存可被安全包裹的最终变体。
    seen: set[str] = set()  # 记录规范化后已出现的变体。
    for variant in variants:  # 依次处理别名、连字符和单复数候选。
        normalized_variant = _normalize_search_term(variant)  # 继续保证别名不会绕过文本清理边界。
        variant_key = normalized_variant.casefold()  # 使用大小写无关键避免 LLM 与 llm 重复。
        if normalized_variant and variant_key not in seen:  # 仅保留首个安全且非重复的变体。
            distinct_variants.append(normalized_variant)  # 保留定义的展示大小写，例如 LLM。
            seen.add(variant_key)  # 标记已使用的等价值。
    return distinct_variants  # 返回稳定顺序的有限变体列表。


def _is_meaningful_arxiv_concept(value: str) -> bool:
    """判断候选是否包含非年份、非通用修饰词的实际检索词。"""
    tokens = _arxiv_term_tokens(value)  # 提取清理后的可比较词项。
    return any(token.casefold() not in _ARXIV_GENERIC_WORDS and not _is_arxiv_year_token(token) for token in tokens)  # 至少一个内容词才允许形成概念组。


def _arxiv_concepts_overlap(first_tokens: set[str], second_tokens: set[str]) -> bool:
    """用小型词项包含度规则识别不应重复形成 AND 组的概念。"""
    if not first_tokens or not second_tokens:  # 空概念不能可靠比较且会在进入此处前被拒绝。
        return False  # 保持防御性边界。
    overlap_ratio = len(first_tokens & second_tokens) / min(len(first_tokens), len(second_tokens))  # 较短概念被较长概念包含时视为高度重复。
    return overlap_ratio >= 0.75  # 保守阈值避免相近词偶然共享一个普通词就被合并。


def _arxiv_concept_tokens(value: str) -> set[str]:
    """提取概念重叠比较所需的大小写无关词项。"""
    return {token.casefold() for token in _arxiv_term_tokens(value) if token.casefold() not in _ARXIV_GENERIC_WORDS and not _is_arxiv_year_token(token)}  # 忽略不会区分学术概念的泛化词与年份。


def _arxiv_term_tokens(value: str) -> list[str]:
    """从安全文本中提取保持连字符的词项序列。"""
    return _ARXIV_TERM_TOKEN_PATTERN.findall(value)  # 不保留括号、引号、冒号或布尔操作符。


def _is_arxiv_year_token(token: str) -> bool:
    """判断单个词项是否是不能独立成为文本概念的四位年份。"""
    return len(token) == 4 and token.isdigit()  # 年份过滤应仅由 submittedDate 子句表达。


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
        source_rate_limiter: SourceRateLimiter | None = None,
        request_executor: AcademicApiRequestExecutor | None = None,
    ) -> None:
        """保存配置、测试传输层与来源级节流状态。"""
        self._settings = settings_override or settings  # 默认复用经环境变量校验的全局配置。
        self._transport = transport  # 保留可由测试替换的 HTTP 传输层。
        self._request_executor = request_executor or AcademicApiRequestExecutor("arxiv", self._settings, self._settings.arxiv_requests_per_second, source_rate_limiter=source_rate_limiter)  # 统一管理请求前的来源窗口和重试。

    async def search(self, query: QueryIntent) -> list[PaperRecord]:
        """搜索 arXiv 并返回保留来源排名的统一论文记录。

        参数：
            query：包含检索词、年份和目标数量的完整查询意图。
        返回：
            list[PaperRecord]：已映射且保留 arXiv 来源溯源信息的论文列表。
        异常：
            ArxivClientError：HTTP、Atom 解析或来源错误响应时抛出。
        """
        params = build_arxiv_search_params(query)  # 构造不含用户密钥的可测试请求参数。
        try:  # 将 HTTP 层异常转换为不泄露响应正文的领域错误。
            async with httpx.AsyncClient(  # 为单次请求创建可自动关闭的异步客户端。
                base_url=self._settings.arxiv_api_base_url,  # 使用集中配置的 arXiv API 地址。
                timeout=self._settings.arxiv_timeout_seconds,  # 使用集中配置的请求超时。
                transport=self._transport,  # 在测试时使用本地 MockTransport。
                headers={"User-Agent": "ScholarWeave/0.1 (academic-search)"},  # 标识客户端用途但不发送用户数据或密钥。
            ) as client:
                response = await self._request_executor.execute(lambda: client.get("/query", params=params))  # 每次重试均重新通过统一来源限流。
                response.raise_for_status()  # 将非成功 HTTP 状态转换为可统一处理的异常。
                entries = parse_arxiv_atom_feed(response.text)  # 解析 Atom XML 并提前识别来源内错误条目。
        except httpx.HTTPStatusError as error:  # 单独记录不含响应正文的 HTTP 状态码。
            logger.error("arXiv 请求失败，状态码=%d", error.response.status_code)  # 输出安全且可观测的来源错误。
            raise ArxivClientError(f"arXiv 请求失败（HTTP {error.response.status_code}）") from None  # 隐藏底层请求上下文。
        except httpx.RequestError as error:  # 捕获连接、超时和传输失败。
            logger.error("arXiv 网络请求失败，错误类型=%s", type(error).__name__)  # 仅记录安全的异常类型。
            raise ArxivClientError("arXiv 网络请求失败") from None  # 返回稳定的领域错误。
        except AcademicApiNetworkError:  # 统一执行器耗尽网络重试后不泄露传输层上下文。
            raise ArxivClientError("arXiv 网络请求失败") from None  # 保留既有来源错误类型。
        except SourceCooldownError:  # 冷却期内不触发任何 HTTP transport 调用。
            raise ArxivClientError("arXiv 请求受限，当前处于冷却期") from None  # 让协调器继续使用其他来源。
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
    without_field_prefixes = _ARXIV_FIELD_PREFIX_PATTERN.sub(" ", value)  # 字段前缀只能由编译器生成，不能接受用户原始语法。
    without_boolean_words = _ARXIV_BOOLEAN_PATTERN.sub(" ", without_field_prefixes)  # 布尔词不能影响概念组之间的固定连接结构。
    safe_tokens = _arxiv_term_tokens(without_boolean_words.replace('"', " ").replace("(", " ").replace(")", " "))  # 移除引号和括号后仅保留可安全重组的词项。
    return " ".join(safe_tokens)  # 统一空白并确保返回值不含 arXiv 查询语法。


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
