"""封装 Tavily 补充网页发现、来源级节流与不可合并结果映射。"""

import asyncio  # 串行控制 Tavily 来源级请求间隔。
from collections.abc import Mapping  # 安全读取嵌套 JSON 对象。

import httpx  # 提供异步 HTTP 客户端和可注入测试传输层。

from backend.app.adapters.base import WebDiscoveryAdapter  # 实现补充网页发现的独立协议。
from backend.app.core.config import Settings, settings  # 读取 Tavily 地址、密钥、超时和限额配置。
from backend.app.core.logging import logger  # 记录不含完整查询和密钥的来源调用统计与错误。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 构造不可合并为论文的网页发现结果。
from backend.app.models.query_intent import QueryIntent  # 接收查询规划节点输出的统一意图。


class TavilyMappingError(ValueError):
    """表示 Tavily 单条网页结果缺少生成补充发现项所必需的数据。"""


class TavilyClientError(RuntimeError):
    """表示 Tavily HTTP 调用、认证或响应结构不可用。"""


def build_tavily_search_payload(query: QueryIntent, max_results: int) -> dict[str, str | int | bool]:
    """将 QueryIntent 转换为 Tavily 补充发现请求体。

    参数：
        query：已由查询规划节点校验的统一检索意图。
        max_results：经配置上限裁剪后的补充网页结果数量。
    返回：
        dict[str, str | int | bool]：不含密钥和完整原始查询的 Tavily JSON 请求体。
    """
    search_terms: list[str] = []  # 按确定顺序收集可用于网页发现的结构化检索词。
    for terms in (query.research_topics, query.methods, query.tasks, query.datasets, query.must_include):  # 合并主题、方法、任务、数据集与硬约束。
        search_terms.extend(term.strip() for term in terms if term.strip())  # 跳过空白项并保持查询规划语义顺序。
    search_text = " ".join(search_terms) or query.normalized_query  # 缺少显式词时回退为规范化查询而非原始用户输入。
    return {  # 返回官方 Tavily Search 端点所需的最小请求体。
        "query": search_text,  # 使用结构化词组合执行补充网页发现。
        "search_depth": "basic",  # 首版使用成本更低的基础检索深度。
        "max_results": max_results,  # 限制补充网页数量避免挤占学术来源预算。
        "topic": "general",  # 使用官方支持的通用网页发现主题。
        "include_answer": False,  # 不请求生成式答案，避免将其混入可溯源证据。
        "include_raw_content": False,  # 不接收完整网页正文，降低敏感内容和存储风险。
        "include_images": False,  # 首版不需要图片结果。
        "include_usage": False,  # 运行统计将在后续统一预算模块中收集。
    }


class TavilyClient(WebDiscoveryAdapter):
    """实现 Tavily 补充网页发现及不可合并结果输出。

    参数：
        settings_override：测试或多环境场景下可替换的配置对象。
        transport：可选 HTTP 传输层，仅用于无网络单元测试或定制网络策略。
    """

    source = "tavily"  # 声明当前客户端实现的补充发现来源名称。

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

    async def discover(self, query: QueryIntent) -> list[SupplementalDiscoveryItem]:
        """执行 Tavily 网页补充发现，并返回不可合并为论文的结果。

        参数：
            query：包含检索词和目标数量的完整查询意图。
        返回：
            list[SupplementalDiscoveryItem]：明确标记不可进入论文去重的网页发现项。
        异常：
            TavilyClientError：配置、HTTP、网络或响应结构异常时抛出。
        """
        await self._wait_for_rate_limit()  # 在请求前遵守配置化的 Tavily 最小间隔。
        max_results = min(query.target_paper_count, self._settings.tavily_max_results)  # 限制网页补充结果不超过配置预算。
        payload = build_tavily_search_payload(query, max_results=max_results)  # 构造不含密钥的可测试 JSON 请求体。
        try:  # 将缺失部署配置转换为适配层领域错误。
            api_key = self._settings.require_tavily_api_key()  # 仅在即将请求时读取并校验密钥。
        except ValueError:  # 不将配置实现细节或密钥内容暴露给上层。
            logger.error("Tavily 服务未配置 API 密钥")  # 记录安全的部署错误信息。
            raise TavilyClientError("Tavily 服务尚未配置") from None  # 返回稳定且不含密钥的领域错误。
        try:  # 将 HTTP 层异常转换为不泄露请求和响应正文的领域错误。
            async with httpx.AsyncClient(  # 为单次请求创建可自动关闭的异步客户端。
                base_url=self._settings.tavily_api_base_url,  # 使用集中配置的 Tavily API 地址。
                timeout=self._settings.tavily_timeout_seconds,  # 使用集中配置的请求超时。
                transport=self._transport,  # 在测试时使用本地 MockTransport。
            ) as client:
                response = await client.post(  # 调用官方 POST Search 端点。
                    "/search",  # 使用配置基地址下的搜索路径。
                    json=payload,  # 发送不含密钥的结构化 JSON 请求体。
                    headers={"Authorization": f"Bearer {api_key}"},  # 仅在 HTTP 请求层注入 Bearer 密钥。
                )
                response.raise_for_status()  # 将非成功 HTTP 状态转换为可统一处理的异常。
                response_data = response.json()  # 解码 JSON 响应供结构校验与映射使用。
        except httpx.HTTPStatusError as error:  # 单独记录不含认证头和响应正文的 HTTP 状态码。
            logger.error("Tavily 请求失败，状态码=%d", error.response.status_code)  # 输出安全且可观测的来源错误。
            raise TavilyClientError(f"Tavily 请求失败（HTTP {error.response.status_code}）") from None  # 隐藏底层请求上下文。
        except httpx.RequestError as error:  # 捕获连接、超时和传输失败。
            logger.error("Tavily 网络请求失败，错误类型=%s", type(error).__name__)  # 仅记录安全的异常类型。
            raise TavilyClientError("Tavily 网络请求失败") from None  # 返回稳定的领域错误。
        except ValueError:  # 捕获无效 JSON 等解析失败。
            logger.error("Tavily 响应不是有效 JSON")  # 不记录可能过大的原始响应正文。
            raise TavilyClientError("Tavily 响应格式无效") from None  # 返回不泄露内部细节的稳定错误。

        results = _extract_tavily_results(response_data)  # 校验并读取官方 JSON 中的网页结果数组。
        discoveries: list[SupplementalDiscoveryItem] = []  # 保存成功映射的不可合并网页发现项。
        skipped_count = 0  # 统计字段不完整而无法映射的单条网页结果数量。
        for raw_rank, result in enumerate(results, start=1):  # 保留来源返回顺序以支持前端解释。
            result_data = _as_mapping(result)  # 确认单条网页结果具有对象结构。
            if result_data is None:  # 非对象条目不能映射为补充发现项。
                skipped_count += 1  # 累加结构异常条目数量。
                continue  # 继续处理同页其余网页结果。
            try:  # 单条映射失败不应丢弃整页可用结果。
                discoveries.append(map_tavily_result(result_data, raw_rank=raw_rank))  # 映射并显式保留不可合并边界。
            except TavilyMappingError:  # 仅跳过缺少标题或 URL 的无效网页结果。
                skipped_count += 1  # 累加映射失败统计。
        logger.info("Tavily 补充发现完成：原始结果=%d，映射成功=%d，跳过=%d", len(results), len(discoveries), skipped_count)  # 记录不含完整查询的阶段统计。
        return discoveries  # 返回不得进入论文融合流程的网页发现结果。

    async def _wait_for_rate_limit(self) -> None:
        """按配置化 RPS 串行等待下一次允许发起 Tavily 请求的时间。"""
        async with self._rate_limit_lock:  # 防止同一客户端并发请求绕过来源级限额。
            loop = asyncio.get_running_loop()  # 使用事件循环单调时间避免系统时钟调整影响间隔。
            now = loop.time()  # 读取当前单调时间。
            wait_seconds = max(0.0, self._next_request_at - now)  # 计算距离允许请求还需等待的时间。
            if wait_seconds > 0:  # 仅在连续调用过快时等待。
                logger.info("Tavily 限流等待：秒数=%.3f", wait_seconds)  # 记录来源级等待统计，不记录查询内容。
                await asyncio.sleep(wait_seconds)  # 让出事件循环并遵守请求最小间隔。
            self._next_request_at = loop.time() + (1.0 / self._settings.tavily_requests_per_second)  # 预约下一次允许请求的时间。


def map_tavily_result(result: Mapping[str, object], raw_rank: int) -> SupplementalDiscoveryItem:
    """将一条 Tavily 网页结果映射为不可合并的 SupplementalDiscoveryItem。

    参数：
        result：已经由 HTTP 客户端解码的单条 Tavily 网页结果对象。
        raw_rank：该网页在当前来源搜索结果中的一开始排名。
    返回：
        SupplementalDiscoveryItem：不会进入论文去重或引用关系流程的补充发现项。
    异常：
        TavilyMappingError：缺少有效标题、HTTP URL 或来源排名时抛出。
    """
    title = _required_text(result, "title")  # 读取可展示的网页标题。
    url = _required_text(result, "url")  # 读取可作为网页证据入口的来源 URL。
    if not url.startswith(("http://", "https://")):  # 补充发现模型只接受可直接访问的 HTTP 或 HTTPS 地址。
        raise TavilyMappingError("Tavily 网页结果缺少有效 HTTP URL")  # 防止非网页协议混入前端链接。
    score = result.get("score")  # 读取来源可选相关性分数。
    relevance_score = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) and score >= 0 else None  # 忽略负数、布尔值或异常分数类型。
    return SupplementalDiscoveryItem(  # 构造固定不可合并的补充网页发现项。
        source="tavily",  # 标记当前网页发现来源。
        title=title,  # 保留来源标题用于前端展示。
        url=url,  # 保留来源网页地址作为外部证据入口。
        snippet=_optional_text(result.get("content")) or "",  # 仅保留来源摘要，不接收完整原文。
        relevance_score=relevance_score,  # 保留经过类型校验的来源相关性分数。
        raw_rank=raw_rank,  # 保留来源原始名次。
    )


def _extract_tavily_results(payload: object) -> list[object]:
    """从 Tavily 搜索响应中校验并提取网页结果数组。"""
    response_data = _as_mapping(payload)  # 确认 JSON 根对象是映射。
    raw_results = response_data.get("results") if response_data else None  # 读取官方响应中的网页结果数组。
    if not isinstance(raw_results, list):  # 缺失或类型异常代表响应结构不符合端点契约。
        logger.error("Tavily 响应缺少 results 数组")  # 记录可定位结构异常但不输出响应正文。
        raise TavilyClientError("Tavily 响应缺少结果列表")  # 阻止错误数据被误判为空网页发现。
    return raw_results  # 返回未经单条映射的来源原始网页结果数组。


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将 JSON 值安全转换为可读取的字符串键映射。"""
    return value if isinstance(value, Mapping) else None  # 拒绝列表、字符串和空值等非对象字段。


def _optional_text(value: object) -> str | None:
    """提取去除首尾空白后的可选网页文本字段。"""
    if not isinstance(value, str):  # 非字符串不能作为网页标题、URL 或摘要。
        return None  # 以空值表示字段缺失或类型异常。
    normalized_text = value.strip()  # 去除来源可能带入的展示空白。
    return normalized_text or None  # 将空字符串统一视为缺失。


def _required_text(data: Mapping[str, object], field_name: str) -> str:
    """读取 Tavily 网页结果中的必要文本字段。"""
    text_value = _optional_text(data.get(field_name))  # 读取并规范化必要字段。
    if text_value is None:  # 标题或 URL 缺失时无法构造可展示补充发现项。
        raise TavilyMappingError(f"Tavily 网页结果缺少有效字段：{field_name}")  # 返回可定位的映射错误。
    return text_value  # 返回已经通过空值校验的文本。
