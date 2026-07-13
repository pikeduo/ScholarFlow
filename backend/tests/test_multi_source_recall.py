"""验证多源召回协调器的并发汇总、结果分流和单源故障降级。"""

import asyncio  # 在同步 pytest 用例中运行异步协调器。

from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离路由配置。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 构造不可合并网页发现测试结果。
from backend.app.models.paper import PaperRecord, PaperSourceRecord  # 构造统一论文测试记录和来源溯源。
from backend.app.models.query_intent import QueryIntent  # 构造协调器所需查询意图。
from backend.app.models.semantic_ranking import SemanticRankingResult  # 构造不加载模型的语义粗排替身结果。
from backend.app.models.cross_encoder_ranking import CrossEncoderRankingResult  # 构造不加载模型的 Cross Encoder 替身结果。
from backend.app.models.llm_ranking import LlmRankingResult  # 构造不访问外部 API 的 LLM 精排替身结果。
from backend.app.services.multi_source_recall import MultiSourceRecallCoordinator  # 导入待测多源召回协调服务。
from backend.app.services.source_router import SourceRouter  # 使用实际确定性路由器生成执行计划。


class _StubAcademicAdapter:
    """提供可返回固定论文或抛出固定异常的离线学术来源替身。"""

    def __init__(self, source: str, papers: list[PaperRecord] | None = None, should_fail: bool = False) -> None:
        """保存来源名称、固定论文和失败开关。"""
        self.source = source  # 保存测试替身对应的来源名称。
        self._papers = papers or []  # 保存成功时应返回的固定论文列表。
        self._should_fail = should_fail  # 保存是否模拟单源调用失败。

    async def search(self, query: QueryIntent) -> list[PaperRecord]:
        """按测试配置返回论文或模拟来源异常。"""
        if self._should_fail:  # 仅在降级测试中模拟单源失败。
            raise RuntimeError("模拟学术来源失败")  # 触发协调器的单源故障隔离逻辑。
        return self._papers  # 返回固定论文而不访问网络。


class _StubWebDiscoveryAdapter:
    """提供固定补充网页发现结果的离线来源替身。"""

    source = "tavily"  # 声明当前替身对应的补充发现来源。

    def __init__(self, discoveries: list[SupplementalDiscoveryItem]) -> None:
        """保存成功时应返回的固定网页发现列表。"""
        self._discoveries = discoveries  # 保存不可合并网页发现结果。

    async def discover(self, query: QueryIntent) -> list[SupplementalDiscoveryItem]:
        """返回固定网页发现而不访问网络。"""
        return self._discoveries  # 保持测试结果确定且独立于外部服务。


class _PassthroughSemanticRanker:
    """在协调器测试中保留候选顺序，避免加载或下载 BGE-M3 模型。"""

    def __init__(self) -> None:
        """记录协调器为每次调用选择的模型阶段开关。"""
        self.enabled_values: list[bool] = []  # 保存标准与深度模式传入的开关，供策略测试断言。

    def rank(self, papers: list[PaperRecord], _: QueryIntent, *, enabled: bool = True, disabled_reason: str | None = None) -> SemanticRankingResult:
        """返回未截断候选，模拟语义阶段已成功完成。"""
        self.enabled_values.append(enabled)  # 记录是否允许当前模式执行语义模型。
        return SemanticRankingResult(papers=papers, input_count=len(papers), truncated_count=0, model_name="test-bge-m3", ranking_error=None if enabled else disabled_reason)  # 构造无需模型的稳定排序结果。


class _PassthroughCrossEncoderReranker:
    """在协调器测试中保留候选顺序，避免加载或下载 Cross Encoder 模型。"""

    def __init__(self) -> None:
        """记录协调器为每次调用选择的模型阶段开关。"""
        self.enabled_values: list[bool] = []  # 保存标准与深度模式传入的开关，供策略测试断言。

    def rerank(self, papers: list[PaperRecord], _: QueryIntent, *, enabled: bool = True, disabled_reason: str | None = None) -> CrossEncoderRankingResult:
        """返回未截断候选，模拟 Cross Encoder 阶段已成功完成。"""
        self.enabled_values.append(enabled)  # 记录是否允许当前模式执行交叉编码模型。
        return CrossEncoderRankingResult(papers=papers, input_count=len(papers), truncated_count=0, model_name="test-reranker", ranking_error=None if enabled else disabled_reason)  # 构造无需模型的稳定精排结果。


class _PassthroughLlmReranker:
    """在协调器测试中保留候选顺序，避免访问 DeepSeek API。"""

    async def rerank(self, papers: list[PaperRecord], _: QueryIntent) -> LlmRankingResult:
        """返回未截断候选，模拟 LLM 核验阶段已成功完成。"""
        return LlmRankingResult(papers=papers, input_count=len(papers), model_name="test-llm", prompt_tokens=12, completion_tokens=4)  # 构造无需网络和密钥且含成本统计的稳定最终结果。


def _build_query_intent(domains: list[str] | None = None, requires_web_evidence: bool = False, search_mode: str = "standard", enable_semantic_ranking: bool = True, enable_cross_encoder_ranking: bool = True) -> QueryIntent:
    """构造可用于多源召回协调测试的最小有效查询意图。

    参数：
        domains：可选领域标签。
        requires_web_evidence：是否启用补充网页发现。
        search_mode：标准或深度检索模式。
        enable_semantic_ranking：深度模式下是否执行 BGE-M3。
        enable_cross_encoder_ranking：深度模式下是否执行 Cross Encoder。
    返回：
        QueryIntent：包含测试所需排序开关的最小有效意图。
    """
    return QueryIntent(  # 构造无需 LLM 或网络的查询规划结果。
        original_query="Transformer forecasting",  # 提供用户原始查询文本。
        normalized_query="Transformer forecasting",  # 提供可复现的规范化查询文本。
        query_language="en",  # 标记查询语言。
        domains=domains or [],  # 注入当前测试需要的领域标签。
        requires_web_evidence=requires_web_evidence,  # 注入当前测试需要的网页证据开关。
        search_mode=search_mode,  # 注入标准或深度模型策略。
        enable_semantic_ranking=enable_semantic_ranking,  # 注入 BGE-M3 用户选择以验证协调器分支。
        enable_cross_encoder_ranking=enable_cross_encoder_ranking,  # 注入 Cross Encoder 用户选择以验证协调器分支。
    )


def _build_paper(source: str, suffix: str) -> PaperRecord:
    """构造来源明确、尚未去重的最小统一论文记录。"""
    return PaperRecord(  # 构造用于验证来源顺序与汇总的论文记录。
        paper_id=f"{source}:{suffix}",  # 提供来源内稳定测试标识。
        title=f"{source} paper {suffix}",  # 提供可展示且互不冲突的测试标题。
        source=source,  # 标记当前论文主来源。
    )


def test_coordinator_collects_selected_sources_and_keeps_web_discoveries_separate() -> None:
    """协调器应汇总路由学术来源，并将 Tavily 结果保留在独立集合。"""
    settings = Settings(  # 构造允许核心语义源和网页补充源进入路由的隔离配置。
        _env_file=None,  # 禁止测试读取用户本地配置值。
        semantic_scholar_api_key="test-semantic-key",  # 注入不具备真实权限的路由可用性测试密钥。
        semantic_scholar_enabled=True,  # 显式启用 Semantic Scholar 路由。
        tavily_api_key="test-tavily-key",  # 注入不具备真实权限的网页来源可用性测试密钥。
    )
    coordinator = MultiSourceRecallCoordinator(  # 使用真实路由器与全部离线来源替身构造协调器。
        source_router=SourceRouter(settings),  # 让测试覆盖实际领域和配置路由规则。
        academic_adapters={  # 注册路由可能选择的全部学术来源替身。
            "openalex": _StubAcademicAdapter("openalex", [_build_paper("openalex", "1")]),  # 模拟综合主源成功返回。
            "arxiv": _StubAcademicAdapter("arxiv", [_build_paper("arxiv", "1")]),  # 模拟预印本来源成功返回。
            "dblp": _StubAcademicAdapter("dblp", [_build_paper("dblp", "1")]),  # 模拟计算机书目来源成功返回。
            "semantic_scholar": _StubAcademicAdapter("semantic_scholar", [_build_paper("semantic_scholar", "1")]),  # 模拟已启用语义来源成功返回。
        },
        web_discovery_adapters={  # 注册独立的网页发现来源替身。
            "tavily": _StubWebDiscoveryAdapter(  # 模拟仅供补充证据展示的网页结果。
                [SupplementalDiscoveryItem(source="tavily", title="网页证据", url="https://example.org/evidence", raw_rank=1)]  # 构造不可合并网页发现项。
            )
        },
        semantic_ranker=_PassthroughSemanticRanker(),  # 注入离线语义替身避免测试加载模型。
        cross_encoder_reranker=_PassthroughCrossEncoderReranker(),  # 注入离线 Cross Encoder 替身避免测试加载模型。
        llm_reranker=_PassthroughLlmReranker(),  # 注入离线 LLM 替身避免测试访问 DeepSeek。
    )
    result = asyncio.run(  # 执行不访问网络的多源协调流程。
        coordinator.recall(_build_query_intent(domains=["machine learning"], requires_web_evidence=True))  # 路由 AI 领域并显式启用网页补充。
    )
    assert [paper.source for paper in result.papers] == ["openalex", "arxiv", "dblp", "semantic_scholar"]  # 验证论文按路由计划顺序汇总。
    assert len(result.discoveries) == 1  # 验证网页发现不混入论文集合且被独立返回。
    assert result.discoveries[0].mergeable_as_paper is False  # 验证补充网页项仍保持不可合并边界。
    assert result.source_counts == {"openalex": 1, "arxiv": 1, "dblp": 1, "semantic_scholar": 1, "tavily": 1}  # 验证来源级成功数量完整可观测。
    assert result.source_errors == {}  # 验证全部替身成功时不存在降级错误。
    assert result.raw_paper_count == 4  # 验证来源数量统计与融合前原始论文数量分离保存。
    assert result.merged_paper_count == 0  # 验证不同论文不会被错误合并。
    assert result.llm_model_name == "test-llm"  # 验证协调器透传实际 LLM 名称。
    assert result.llm_prompt_tokens == 12 and result.llm_completion_tokens == 4  # 验证协调器透传 LLM Token 统计。
    assert result.coverage_report is not None  # 验证最终核验后会生成可供后续多轮控制器消费的覆盖报告。
    assert result.coverage_report.should_continue is True  # 验证首轮高相关论文不足目标时报告建议继续而不自行发起调用。


def test_coordinator_respects_deep_mode_and_individual_local_ranking_options() -> None:
    """标准模式强制跳过模型，深度模式分别尊重两个用户选择。"""
    semantic_ranker = _PassthroughSemanticRanker()  # 记录 BGE-M3 阶段收到的模式开关。
    cross_encoder_reranker = _PassthroughCrossEncoderReranker()  # 记录 Cross Encoder 阶段收到的模式开关。
    coordinator = MultiSourceRecallCoordinator(  # 装配只含一个离线来源和三个可观测排序替身的协调器。
        source_router=SourceRouter(Settings(_env_file=None)),  # 使用确定性默认来源路由。
        academic_adapters={"openalex": _StubAcademicAdapter("openalex", [_build_paper("openalex", "1")])},  # 提供不访问网络的最小候选集合。
        semantic_ranker=semantic_ranker,  # 注入可观测 BGE-M3 替身。
        cross_encoder_reranker=cross_encoder_reranker,  # 注入可观测 Cross Encoder 替身。
        llm_reranker=_PassthroughLlmReranker(),  # 保持两种模式均执行 LLM 核验替身。
    )

    standard_result = asyncio.run(coordinator.recall(_build_query_intent(search_mode="standard")))  # 执行标准模式并验证本地模型跳过。
    deep_result = asyncio.run(coordinator.recall(_build_query_intent(search_mode="deep")))  # 执行深度模式并验证默认本地模型获准运行。
    opted_out_result = asyncio.run(coordinator.recall(_build_query_intent(search_mode="deep", enable_semantic_ranking=False, enable_cross_encoder_ranking=False)))  # 执行深度模式并验证两个用户开关可分别关闭模型。

    assert semantic_ranker.enabled_values == [False, True, False]  # 验证 BGE-M3 仅在深度模式且用户允许时进入执行路径。
    assert cross_encoder_reranker.enabled_values == [False, True, False]  # 验证 Cross Encoder 仅在深度模式且用户允许时进入执行路径。
    assert standard_result.semantic_ranking_error == "标准模式已跳过 BGE-M3 语义粗排，已按 RRF 排序"  # 验证标准模式返回可展示的跳过说明。
    assert standard_result.cross_encoder_ranking_error == "标准模式已跳过 Cross Encoder 重排，已沿用 RRF 排序"  # 验证标准模式不伪装为模型故障。
    assert standard_result.llm_model_name == "test-llm" and standard_result.llm_prompt_tokens == 12  # 验证标准模式仍会执行 LLM 核验阶段。
    assert deep_result.semantic_ranking_error is None and deep_result.cross_encoder_ranking_error is None  # 验证深度模式不会报告主动跳过。
    assert opted_out_result.semantic_ranking_error == "用户已关闭 BGE-M3 语义粗排，已按 RRF 排序"  # 验证用户关闭语义粗排时返回可展示摘要。
    assert opted_out_result.cross_encoder_ranking_error == "用户已关闭 Cross Encoder 重排，已沿用 BGE-M3 或 RRF 排序"  # 验证用户关闭精排时返回可展示摘要。


def test_coordinator_degrades_single_academic_source_without_discarding_other_results() -> None:
    """任一学术来源失败时协调器应保留其余来源结果并记录安全错误摘要。"""
    settings = Settings(  # 构造路由 OpenAlex 与 Semantic Scholar 的隔离配置。
        _env_file=None,  # 禁止测试读取用户本地配置值。
        semantic_scholar_api_key="test-semantic-key",  # 注入路由可用性测试密钥。
        semantic_scholar_enabled=True,  # 显式启用 Semantic Scholar 路由。
    )
    coordinator = MultiSourceRecallCoordinator(  # 使用一个成功来源和一个失败来源替身构造协调器。
        source_router=SourceRouter(settings),  # 使用实际核心双源路由规则。
        academic_adapters={  # 仅注册当前路由会选择的两个核心来源。
            "openalex": _StubAcademicAdapter("openalex", [_build_paper("openalex", "1")]),  # 模拟主源成功返回。
            "semantic_scholar": _StubAcademicAdapter("semantic_scholar", should_fail=True),  # 模拟语义来源调用失败。
        },
        semantic_ranker=_PassthroughSemanticRanker(),  # 注入离线语义替身避免测试加载模型。
        cross_encoder_reranker=_PassthroughCrossEncoderReranker(),  # 注入离线 Cross Encoder 替身避免测试加载模型。
        llm_reranker=_PassthroughLlmReranker(),  # 注入离线 LLM 替身避免测试访问 DeepSeek。
    )
    result = asyncio.run(coordinator.recall(_build_query_intent()))  # 执行不访问网络的核心双源协调流程。
    assert [paper.source for paper in result.papers] == ["openalex"]  # 验证失败来源不会丢弃主源已返回论文。
    assert result.source_counts == {"openalex": 1, "semantic_scholar": 0}  # 验证失败来源仍记录零结果统计。
    assert result.source_errors == {"semantic_scholar": "学术来源调用失败"}  # 验证调用方仅收到不含内部细节的降级摘要。
    assert result.raw_paper_count == 1  # 验证失败来源不会虚构进入融合阶段的论文。


def test_coordinator_reports_unregistered_selected_source_without_raising() -> None:
    """路由选中但未注册适配器时协调器应记录配置降级而不抛出异常。"""
    coordinator = MultiSourceRecallCoordinator(  # 构造没有 OpenAlex 适配器注册的协调器。
        source_router=SourceRouter(Settings(_env_file=None)),  # 使用必然选择 OpenAlex 的隔离默认路由器。
        academic_adapters={},  # 故意留空以模拟应用装配遗漏。
        semantic_ranker=_PassthroughSemanticRanker(),  # 注入离线语义替身保持测试无模型依赖。
        cross_encoder_reranker=_PassthroughCrossEncoderReranker(),  # 注入离线 Cross Encoder 替身保持测试无模型依赖。
        llm_reranker=_PassthroughLlmReranker(),  # 注入离线 LLM 替身避免测试访问 DeepSeek。
    )
    result = asyncio.run(coordinator.recall(_build_query_intent()))  # 执行并触发未注册来源降级。
    assert result.papers == []  # 验证没有适配器时不会虚构论文结果。
    assert result.source_errors == {"openalex": "学术来源适配器未注册"}  # 验证返回稳定配置错误摘要。


def test_coordinator_fuses_cross_source_duplicate_before_returning_result() -> None:
    """协调器应在返回 API 边界前融合 DOI 重复论文，并保留来源级原始数量统计。"""
    openalex_paper = PaperRecord(  # 构造 OpenAlex 返回的 DOI 论文。
        paper_id="https://openalex.org/W-duplicate",  # 提供 OpenAlex 稳定标识。
        title="Duplicate Paper",  # 提供同一论文标题。
        source="openalex",  # 标记主来源。
        doi="10.1000/duplicate",  # 提供跨来源 DOI。
        source_records=[PaperSourceRecord(source="openalex", external_id="https://openalex.org/W-duplicate", raw_rank=1)],  # 提供 RRF 所需来源排名。
    )
    semantic_paper = PaperRecord(  # 构造 Semantic Scholar 返回的同 DOI 论文。
        paper_id="S2-duplicate",  # 提供 Semantic Scholar 稳定标识。
        title="Duplicate Paper",  # 提供同一论文标题。
        source="semantic_scholar",  # 标记补充来源。
        doi="https://doi.org/10.1000/DUPLICATE",  # 使用不同 DOI 展示形式验证规范化融合。
        source_records=[PaperSourceRecord(source="semantic_scholar", external_id="S2-duplicate", raw_rank=2)],  # 提供第二来源的 RRF 排名。
    )
    settings = Settings(  # 构造允许核心双源进入路由的隔离配置。
        _env_file=None,  # 禁止读取本地真实配置。
        semantic_scholar_api_key="test-semantic-key",  # 注入仅用于路由的测试密钥。
        semantic_scholar_enabled=True,  # 显式启用核心补充来源。
    )
    coordinator = MultiSourceRecallCoordinator(  # 使用离线学术来源替身构造协调器。
        source_router=SourceRouter(settings),  # 使用实际核心双源路由规则。
        academic_adapters={  # 注册两个会被当前路由选择的离线来源。
            "openalex": _StubAcademicAdapter("openalex", [openalex_paper]),  # 返回首条来源论文。
            "semantic_scholar": _StubAcademicAdapter("semantic_scholar", [semantic_paper]),  # 返回相同 DOI 的补充来源论文。
        },
        semantic_ranker=_PassthroughSemanticRanker(),  # 注入离线语义替身避免测试加载模型。
        cross_encoder_reranker=_PassthroughCrossEncoderReranker(),  # 注入离线 Cross Encoder 替身避免测试加载模型。
        llm_reranker=_PassthroughLlmReranker(),  # 注入离线 LLM 替身避免测试访问 DeepSeek。
    )

    result = asyncio.run(coordinator.recall(_build_query_intent()))  # 执行不访问网络的召回和融合流程。

    assert result.raw_paper_count == 2  # 验证记录融合前的两个来源原始响应。
    assert len(result.papers) == 1  # 验证重复论文不会透传到多源结果。
    assert result.merged_paper_count == 1  # 验证返回合并掉的一条重复来源记录。
    assert [source_record.source for source_record in result.papers[0].source_records] == ["openalex", "semantic_scholar"]  # 验证融合结果保留完整来源溯源。


def test_coordinator_filters_fused_papers_before_returning_result() -> None:
    """协调器应在融合后应用 QueryIntent 硬约束并返回可解释过滤统计。"""
    coordinator = MultiSourceRecallCoordinator(  # 构造仅使用 OpenAlex 离线替身的最小协调器。
        source_router=SourceRouter(Settings(_env_file=None)),  # 使用固定选择 OpenAlex 的隔离路由器。
        academic_adapters={"openalex": _StubAcademicAdapter("openalex", [_build_paper("openalex", "missing-term")])},  # 返回不含必须词的论文。
        semantic_ranker=_PassthroughSemanticRanker(),  # 注入离线语义替身避免测试加载模型。
        cross_encoder_reranker=_PassthroughCrossEncoderReranker(),  # 注入离线 Cross Encoder 替身避免测试加载模型。
        llm_reranker=_PassthroughLlmReranker(),  # 注入离线 LLM 替身避免测试访问 DeepSeek。
    )
    query = QueryIntent(  # 构造带必须词硬约束的检索意图。
        original_query="Transformer forecasting",  # 提供原始查询。
        normalized_query="Transformer forecasting",  # 提供规范化查询。
        query_language="en",  # 标记查询语言。
        must_include=["transformer"],  # 要求论文标题或摘要包含指定方法词。
    )

    result = asyncio.run(coordinator.recall(query))  # 执行离线召回、融合和规则过滤。

    assert result.raw_paper_count == 1  # 验证论文已进入融合与过滤流程。
    assert result.papers == []  # 验证不满足必须词的论文不会进入最终候选。
    assert result.filtered_paper_count == 1  # 验证过滤统计记录一条移除。
    assert result.filter_reason_counts == {"must_include": 1}  # 验证返回稳定且可展示的过滤原因。
