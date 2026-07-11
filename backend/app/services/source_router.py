"""根据 QueryIntent、来源策略和配置生成可审计的动态来源路由计划。"""

from backend.app.core.config import Settings, settings  # 读取 Semantic Scholar 与 Tavily 的启用状态和密钥可用性。
from backend.app.core.logging import logger  # 记录不含完整查询的来源选择与降级统计。
from backend.app.models.query_intent import QueryIntent  # 接收查询规划节点输出的统一意图。
from backend.app.models.source_routing import SourceRoutePlan  # 输出可供后续协调服务消费的来源计划。


COMPUTER_SCIENCE_DOMAINS = frozenset(  # 声明首版需要加入 arXiv 与 DBLP 的领域标签集合。
    {
        "ai",  # 覆盖常见人工智能领域缩写。
        "artificial intelligence",  # 覆盖完整人工智能领域名称。
        "computer science",  # 覆盖通用计算机科学领域名称。
        "cs",  # 覆盖常见计算机科学缩写。
        "data mining",  # 覆盖数据挖掘方向。
        "machine learning",  # 覆盖机器学习方向。
        "nlp",  # 覆盖自然语言处理常见缩写。
        "natural language processing",  # 覆盖自然语言处理完整名称。
        "computer vision",  # 覆盖计算机视觉方向。
        "software engineering",  # 覆盖软件工程方向。
    }
)


class SourceRouter:
    """封装来源选择策略，不执行 HTTP 请求或论文融合。

    参数：
        settings_override：测试或多环境场景下可替换的配置对象。
    """

    def __init__(self, settings_override: Settings | None = None) -> None:
        """保存只读配置，确保路由决策可在测试中隔离。"""
        self._settings = settings_override or settings  # 默认复用经过环境变量校验的全局配置。

    def route(self, query: QueryIntent) -> SourceRoutePlan:
        """为一次查询生成学术来源和补充网页来源的可审计选择计划。

        参数：
            query：包含领域标签和网页证据需求的完整查询意图。
        返回：
            SourceRoutePlan：已选来源、补充来源及未启用原因。
        """
        academic_sources = ["openalex"]  # OpenAlex 始终作为首版综合学术检索主源。
        web_discovery_sources: list[str] = []  # 网页补充来源独立保存，绝不混入论文来源。
        selection_reasons = {"openalex": "固定主学术来源，提供综合论文元数据"}  # 保存主源的稳定选择理由。
        unavailable_reasons: dict[str, str] = {}  # 累积未启用来源的安全降级原因。
        normalized_domains = _normalize_domains(query.domains)  # 规范化领域标签以进行不区分大小写的策略匹配。

        if normalized_domains & COMPUTER_SCIENCE_DOMAINS:  # AI 或计算机领域需要预印本与计算机文献补充覆盖。
            academic_sources.extend(["arxiv", "dblp"])  # 仅在相关领域加入两个动态学术来源。
            selection_reasons["arxiv"] = "AI/计算机领域补充预印本与最新研究"  # 记录 arXiv 被选择的业务原因。
            selection_reasons["dblp"] = "AI/计算机领域补充会议与期刊书目元数据"  # 记录 DBLP 被选择的业务原因。

        if self._settings.semantic_scholar_enabled and self._settings.semantic_scholar_api_key is not None:  # 仅在人工启用且密钥实际存在时恢复该来源。
            academic_sources.append("semantic_scholar")  # 将已获批的语义与引文补充源加入学术来源计划。
            selection_reasons["semantic_scholar"] = "已显式启用且 API Key 已配置的语义与引文补充来源"  # 记录安全的恢复原因。
        elif self._settings.semantic_scholar_api_key is None:  # 当前仍处于 API Key 未获批或未配置状态。
            unavailable_reasons["semantic_scholar"] = "尚未配置 API Key，当前不进入路由"  # 避免后续协调器发起必然失败的调用。
        else:  # 密钥已存在但尚未收到用户的显式恢复授权。
            unavailable_reasons["semantic_scholar"] = "尚未显式启用，当前不进入路由"  # 保持默认关闭以避免意外消耗配额。

        if query.requires_web_evidence and self._settings.tavily_api_key is not None:  # 网页证据必须由查询显式请求且配置可用。
            web_discovery_sources.append("tavily")  # 将 Tavily 仅加入独立补充发现通道。
            selection_reasons["tavily"] = "查询显式需要网页补充证据，结果不可合并为论文"  # 记录不混入论文集合的边界说明。
        elif query.requires_web_evidence:  # 查询需要网页证据但本地尚未提供 Tavily Key。
            unavailable_reasons["tavily"] = "查询需要网页证据，但尚未配置 API Key"  # 让前端可显示可理解的降级状态。

        plan = SourceRoutePlan(  # 构造经过 Pydantic 校验的可审计来源选择计划。
            academic_sources=academic_sources,  # 保存可进入论文召回与融合的学术来源。
            web_discovery_sources=web_discovery_sources,  # 保存独立的不可合并网页来源。
            selection_reasons=selection_reasons,  # 保存已选来源的安全业务理由。
            unavailable_reasons=unavailable_reasons,  # 保存未启用来源的安全降级原因。
        )
        logger.info("动态来源路由完成：学术来源数=%d，网页补充来源数=%d，未启用来源数=%d", len(plan.academic_sources), len(plan.web_discovery_sources), len(plan.unavailable_reasons))  # 记录不含原始查询与密钥的阶段统计。
        return plan  # 返回供下一阶段多源召回协调服务消费的路由计划。


def _normalize_domains(domains: list[str]) -> set[str]:
    """压缩空白并规范化 QueryIntent 领域标签用于确定性策略匹配。"""
    return {" ".join(domain.split()).casefold() for domain in domains if domain.strip()}  # 忽略空白标签并保留唯一规范化集合。
