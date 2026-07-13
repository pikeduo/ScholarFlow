"""验证动态来源路由对领域、配置与网页证据开关的确定性选择。"""

from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离配置。
from backend.app.models.query_intent import QueryIntent  # 构造路由器所需的统一查询意图。
from backend.app.services.source_router import SourceRouter  # 导入待测来源路由服务。


def _build_query_intent(
    domains: list[str] | None = None,
    requires_web_evidence: bool = False,
) -> QueryIntent:
    """构造可用于来源路由测试的最小有效查询意图。"""
    return QueryIntent(  # 构造不依赖 LLM、网络或用户本地配置的测试意图。
        original_query="Transformer forecasting",  # 提供用户原始查询文本。
        normalized_query="Transformer forecasting",  # 提供可复现的规范化查询文本。
        query_language="en",  # 标记查询语言。
        domains=domains or [],  # 注入当前测试需要的领域标签。
        requires_web_evidence=requires_web_evidence,  # 注入当前测试需要的网页证据开关。
    )


def test_router_uses_openalex_only_for_unmatched_domains() -> None:
    """没有匹配 AI/计算机领域时路由器只应选择 OpenAlex 主源。"""
    router = SourceRouter(Settings(_env_file=None))  # 使用不含外部来源密钥的隔离默认配置。
    plan = router.route(_build_query_intent(domains=["economics"]))  # 路由不匹配任何动态学术来源策略的领域。
    assert plan.academic_sources == ["openalex"]  # 验证不会无条件调用所有学术来源。
    assert plan.web_discovery_sources == []  # 验证默认不调用补充网页发现来源。
    assert "semantic_scholar" in plan.unavailable_reasons  # 验证待审批语义来源的降级状态可被审计。


def test_router_adds_pubmed_only_for_biomedical_domains() -> None:
    """医学或生命科学领域应按需加入 PubMed，而非让所有搜索都访问 E-utilities。"""
    router = SourceRouter(Settings(_env_file=None))  # PubMed 匿名访问无需 API Key，测试只验证领域路由策略。
    plan = router.route(_build_query_intent(domains=["Clinical Medicine", "biology"]))  # 构造与 PubMed 策略匹配的领域标签。
    assert plan.academic_sources == ["openalex", "pubmed"]  # 验证 PubMed 按固定顺序作为生物医学补充来源加入。
    assert "pubmed" in plan.selection_reasons  # 验证前端可展示 PubMed 被选择的业务原因。


def test_router_adds_arxiv_dblp_and_tavily_only_when_requested() -> None:
    """AI/计算机领域应加入 arXiv、DBLP，网页证据开关才允许 Tavily。"""
    settings = Settings(_env_file=None, tavily_api_key="test-api-key")  # 注入没有真实权限但可用于路由可用性判断的测试密钥。
    router = SourceRouter(settings)  # 使用带 Tavily 配置的隔离路由器。
    plan = router.route(_build_query_intent(domains=["Machine Learning", "computer science"], requires_web_evidence=True))  # 路由计算机领域并显式请求网页证据。
    assert plan.academic_sources == ["openalex", "arxiv", "dblp"]  # 验证动态学术来源按固定顺序加入一次。
    assert plan.web_discovery_sources == ["tavily"]  # 验证 Tavily 只进入独立网页发现通道。
    assert "tavily" in plan.selection_reasons  # 验证网页补充边界具有可展示的选择理由。


def test_router_reports_missing_tavily_key_without_selecting_web_discovery() -> None:
    """请求网页证据但缺少 Key 时应降级而不发起 Tavily 调用。"""
    router = SourceRouter(Settings(_env_file=None))  # 使用不含 Tavily 密钥的隔离默认配置。
    plan = router.route(_build_query_intent(requires_web_evidence=True))  # 路由显式请求网页证据的查询。
    assert plan.web_discovery_sources == []  # 验证未配置来源不会被加入执行计划。
    assert "tavily" in plan.unavailable_reasons  # 验证前端可读取安全的缺失配置说明。


def test_router_restores_semantic_scholar_only_after_explicit_enablement() -> None:
    """Semantic Scholar 必须同时具备 API Key 与显式启用开关才可进入路由。"""
    disabled_settings = Settings(_env_file=None, semantic_scholar_api_key="test-api-key", semantic_scholar_enabled=False)  # 构造密钥存在但未获显式恢复授权的配置。
    enabled_settings = Settings(_env_file=None, semantic_scholar_api_key="test-api-key", semantic_scholar_enabled=True)  # 构造密钥存在且已获显式恢复授权的配置。
    disabled_plan = SourceRouter(disabled_settings).route(_build_query_intent())  # 生成默认关闭时的路由计划。
    enabled_plan = SourceRouter(enabled_settings).route(_build_query_intent())  # 生成显式启用后的路由计划。
    assert "semantic_scholar" not in disabled_plan.academic_sources  # 验证仅有密钥不足以恢复来源调用。
    assert "semantic_scholar" in disabled_plan.unavailable_reasons  # 验证默认关闭原因可被审计。
    assert enabled_plan.academic_sources == ["openalex", "semantic_scholar"]  # 验证同时满足两项条件后才按固定顺序加入来源。
