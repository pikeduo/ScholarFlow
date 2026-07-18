"""验证排序前候选生成服务的融合、过滤、来源分流和故障降级边界。"""

import asyncio  # 在同步 pytest 用例中执行异步候选生成服务。

from backend.app.models.discovery import SupplementalDiscoveryItem  # 构造独立网页发现测试结果。
from backend.app.models.paper import PaperRecord, PaperSourceRecord  # 构造已规范化来源论文和 RRF 溯源。
from backend.app.models.query_intent import QueryIntent  # 构造候选生成服务所需查询意图。
from backend.app.models.source_routing import SourceRoutePlan  # 构造固定学术和网页来源计划。
from backend.app.services.candidate_generation import CandidateGenerationService  # 导入待测排序前候选生成服务。


class _FixedRouter:
    """返回测试显式提供的固定来源计划。"""

    def __init__(self, route_plan: SourceRoutePlan) -> None:
        """保存无需配置或网络判断的来源计划。"""
        self._route_plan = route_plan  # 保存每次调用应返回的确定性计划。

    def route(self, _: QueryIntent) -> SourceRoutePlan:
        """返回固定来源计划且不读取环境配置。"""
        return self._route_plan  # 让测试专注候选生成阶段而非路由规则。


class _AcademicAdapter:
    """返回固定论文或模拟来源异常的离线学术适配器。"""

    def __init__(self, papers: list[PaperRecord] | None = None, should_fail: bool = False) -> None:
        """保存固定论文和失败开关。"""
        self._papers = list(papers or [])  # 复制论文列表避免测试调用间共享可变集合。
        self._should_fail = should_fail  # 保存是否模拟外部来源故障。

    async def search(self, _: QueryIntent) -> list[PaperRecord]:
        """按测试配置返回论文或抛出异常。"""
        if self._should_fail:  # 仅在降级测试中触发来源异常。
            raise RuntimeError("模拟学术来源失败")  # 验证服务将异常隔离为安全摘要。
        return list(self._papers)  # 返回固定副本且不访问网络。


class _WebAdapter:
    """返回固定补充网页发现的离线适配器。"""

    def __init__(self, discoveries: list[SupplementalDiscoveryItem]) -> None:
        """保存固定网页发现集合。"""
        self._discoveries = list(discoveries)  # 复制集合避免测试间状态污染。

    async def discover(self, _: QueryIntent) -> list[SupplementalDiscoveryItem]:
        """返回固定网页发现且不访问网络。"""
        return list(self._discoveries)  # 保持补充发现与论文集合分离。


def _query() -> QueryIntent:
    """构造同时覆盖必须词和排除词规则的查询意图。"""
    return QueryIntent(original_query="Transformer retrieval", normalized_query="Transformer retrieval", query_language="en", must_include=["transformer"], exclude=["blocked"])  # 使用确定性文本规则而不依赖模型。


def test_candidate_generation_fuses_filters_and_separates_web_discoveries() -> None:
    """服务应在模型排序前完成身份融合、RRF、规则过滤和来源类别分流。"""
    openalex_duplicate = PaperRecord(paper_id="W1", title="Transformer Retrieval", source="openalex", doi="10.1000/shared", source_records=[PaperSourceRecord(source="openalex", external_id="W1", raw_rank=1)])  # 构造 DOI 重复论文的主来源记录。
    semantic_duplicate = PaperRecord(paper_id="S1", title="Transformer Retrieval", source="semantic_scholar", doi="https://doi.org/10.1000/SHARED", source_records=[PaperSourceRecord(source="semantic_scholar", external_id="S1", raw_rank=2)])  # 构造同一 DOI 的补充来源记录。
    blocked_paper = PaperRecord(paper_id="W2", title="Transformer Blocked Retrieval", source="openalex", source_records=[PaperSourceRecord(source="openalex", external_id="W2", raw_rank=3)])  # 构造命中排除词的独立候选。
    route_plan = SourceRoutePlan(academic_sources=["openalex", "semantic_scholar"], web_discovery_sources=["tavily"])  # 固定双学术来源和单网页来源。
    service = CandidateGenerationService(  # 使用全部离线替身装配候选生成服务。
        source_router=_FixedRouter(route_plan),  # 注入固定来源计划。
        academic_adapters={"openalex": _AcademicAdapter([openalex_duplicate, blocked_paper]), "semantic_scholar": _AcademicAdapter([semantic_duplicate])},  # 注入三条已规范化来源记录。
        web_discovery_adapters={"tavily": _WebAdapter([SupplementalDiscoveryItem(source="tavily", title="Evidence", url="https://example.org/evidence", raw_rank=1)])},  # 注入一条不可合并网页发现。
    )

    result = asyncio.run(service.generate(_query()))  # 执行不访问网络或任何模型的候选生成流程。

    assert result.academic_source_counts == {"openalex": 2, "semantic_scholar": 1}  # 验证学术来源统计只计算 PaperRecord。
    assert result.web_discovery_source_counts == {"tavily": 1}  # 验证网页发现数量保持独立。
    assert result.source_counts == {"openalex": 2, "semantic_scholar": 1, "tavily": 1}  # 验证兼容公共响应时可合并来源统计。
    assert result.normalized_candidate_count == 3  # 验证成功映射记录数不冒充供应商原始响应数量。
    assert result.deduplicated_candidate_count == 2  # 验证 DOI 重复论文完成身份融合。
    assert result.merged_candidate_count == 1  # 验证融合统计记录一条重复来源记录。
    assert result.filtered_candidate_count == 1  # 验证排除词规则移除一篇论文。
    assert result.filter_reason_counts == {"exclude": 1}  # 验证过滤原因可审计。
    assert len(result.papers) == 1  # 验证仅保留规则过滤后的 BGE-M3 输入候选。
    assert [record.source for record in result.papers[0].source_records] == ["openalex", "semantic_scholar"]  # 验证保留融合后的完整来源溯源。
    assert len(result.discoveries) == 1 and result.discoveries[0].mergeable_as_paper is False  # 验证网页发现永不进入论文候选。
    assert result.source_errors == {}  # 验证全部替身成功时没有来源降级。


def test_candidate_generation_returns_empty_candidates_when_source_fails() -> None:
    """单一学术来源异常应降级为空候选并保留零计数和安全错误。"""
    route_plan = SourceRoutePlan(academic_sources=["openalex"])  # 构造不含网页来源的最小计划。
    service = CandidateGenerationService(source_router=_FixedRouter(route_plan), academic_adapters={"openalex": _AcademicAdapter(should_fail=True)})  # 注入会失败但不访问网络的学术替身。

    result = asyncio.run(service.generate(_query()))  # 执行来源故障降级流程。

    assert result.papers == [] and result.discoveries == []  # 验证服务不会虚构论文或网页结果。
    assert result.academic_source_counts == {"openalex": 0}  # 验证失败来源仍有明确零计数。
    assert result.normalized_candidate_count == 0 and result.deduplicated_candidate_count == 0  # 验证空输入安全通过融合和过滤。
    assert result.academic_source_errors == {"openalex": "学术来源调用失败"}  # 验证只返回不含内部细节的错误摘要。


def test_candidate_generation_reports_unregistered_sources_without_raising() -> None:
    """路由已选但未注册的学术和网页来源应分别降级且不互相污染。"""
    route_plan = SourceRoutePlan(academic_sources=["openalex"], web_discovery_sources=["tavily"])  # 构造两类来源均缺少适配器的计划。
    service = CandidateGenerationService(source_router=_FixedRouter(route_plan), academic_adapters={}, web_discovery_adapters={})  # 故意留空注册表模拟组合根遗漏。

    result = asyncio.run(service.generate(_query()))  # 执行无需网络的未注册来源降级流程。

    assert result.academic_source_counts == {"openalex": 0} and result.web_discovery_source_counts == {"tavily": 0}  # 验证两类来源分别保留零计数。
    assert result.academic_source_errors == {"openalex": "学术来源适配器未注册"}  # 验证学术来源错误保持独立。
    assert result.web_discovery_source_errors == {"tavily": "补充网页来源适配器未注册"}  # 验证网页来源错误保持独立。
