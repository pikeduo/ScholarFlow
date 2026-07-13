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
    query = _build_query_intent(domains=["Clinical Medicine", "biology"])  # 构造与 PubMed 策略匹配的领域标签。
    first_round_plan = router.route(query)  # 生成优先使用综合主源的首轮计划。
    second_round_plan = router.route(query.model_copy(update={"retrieval_round": 2}))  # 生成使用生物医学补充源的第二轮计划。
    assert first_round_plan.academic_sources == ["openalex"]  # 验证首轮不并行调用所有候选来源。
    assert second_round_plan.academic_sources == ["pubmed"]  # 验证 PubMed 在后续轮次作为生物医学补充来源加入。
    assert "pubmed" in second_round_plan.selection_reasons  # 验证前端可展示 PubMed 被选择的业务原因。


def test_router_reserves_the_third_relevant_academic_source_for_the_third_round() -> None:
    """AI/计算机领域前两轮应使用主候选，第三轮才单独调用 DBLP。"""
    settings = Settings(_env_file=None, tavily_api_key="test-api-key")  # 注入没有真实权限但可用于路由可用性判断的测试密钥。
    router = SourceRouter(settings)  # 使用带 Tavily 配置的隔离路由器。
    first_round_plan = router.route(_build_query_intent(domains=["Machine Learning", "computer science"], requires_web_evidence=True))  # 首轮只路由两个相关主候选并显式启用网页证据。
    second_round_plan = router.route(_build_query_intent(domains=["Machine Learning", "computer science"], requires_web_evidence=True).model_copy(update={"retrieval_round": 2}))  # 第二轮保持主候选组合以配合不同子查询。
    third_round_plan = router.route(_build_query_intent(domains=["Machine Learning", "computer science"], requires_web_evidence=True).model_copy(update={"retrieval_round": 3}))  # 第三轮切换到此前未用的领域书目来源。
    assert first_round_plan.academic_sources == ["openalex"]  # 验证首轮只调用综合主源，不无条件调用全部学术来源。
    assert second_round_plan.academic_sources == ["arxiv"]  # 验证第二轮切换到下一相关来源，避免重复消耗首轮来源配额。
    assert third_round_plan.academic_sources == ["dblp"]  # 验证第三轮单独调用领域相关的第三来源。
    assert third_round_plan.web_discovery_sources == ["tavily"]  # 验证 Tavily 仍只进入独立网页发现通道。
    assert "dblp" in third_round_plan.selection_reasons and "tavily" in third_round_plan.selection_reasons  # 验证实际调用的第三来源和网页补充来源均保留可展示理由。


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
    query = _build_query_intent()  # 构造可分别验证首轮和第二轮的统一查询意图。
    disabled_plan = SourceRouter(disabled_settings).route(query)  # 生成默认关闭时的路由计划。
    enabled_first_round_plan = SourceRouter(enabled_settings).route(query)  # 生成显式启用后的首轮路由计划。
    enabled_second_round_plan = SourceRouter(enabled_settings).route(query.model_copy(update={"retrieval_round": 2}))  # 生成显式启用后的第二轮路由计划。
    assert "semantic_scholar" not in disabled_plan.academic_sources  # 验证仅有密钥不足以恢复来源调用。
    assert "semantic_scholar" in disabled_plan.unavailable_reasons  # 验证默认关闭原因可被审计。
    assert enabled_first_round_plan.academic_sources == ["openalex"]  # 验证首轮仍优先调用综合主源。
    assert enabled_second_round_plan.academic_sources == ["semantic_scholar"]  # 验证同时满足两项条件后，第二轮才调用受限语义来源。
