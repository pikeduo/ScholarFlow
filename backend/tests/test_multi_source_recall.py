"""验证多源召回协调器的并发汇总、结果分流和单源故障降级。"""

import asyncio  # 在同步 pytest 用例中运行异步协调器。

from backend.app.core.config import Settings  # 构造不读取真实 .env 的隔离路由配置。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 构造不可合并网页发现测试结果。
from backend.app.models.paper import PaperRecord  # 构造统一论文测试记录。
from backend.app.models.query_intent import QueryIntent  # 构造协调器所需查询意图。
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


def _build_query_intent(domains: list[str] | None = None, requires_web_evidence: bool = False) -> QueryIntent:
    """构造可用于多源召回协调测试的最小有效查询意图。"""
    return QueryIntent(  # 构造无需 LLM 或网络的查询规划结果。
        original_query="Transformer forecasting",  # 提供用户原始查询文本。
        normalized_query="Transformer forecasting",  # 提供可复现的规范化查询文本。
        query_language="en",  # 标记查询语言。
        domains=domains or [],  # 注入当前测试需要的领域标签。
        requires_web_evidence=requires_web_evidence,  # 注入当前测试需要的网页证据开关。
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
    )
    result = asyncio.run(  # 执行不访问网络的多源协调流程。
        coordinator.recall(_build_query_intent(domains=["machine learning"], requires_web_evidence=True))  # 路由 AI 领域并显式启用网页补充。
    )
    assert [paper.source for paper in result.papers] == ["openalex", "arxiv", "dblp", "semantic_scholar"]  # 验证论文按路由计划顺序汇总。
    assert len(result.discoveries) == 1  # 验证网页发现不混入论文集合且被独立返回。
    assert result.discoveries[0].mergeable_as_paper is False  # 验证补充网页项仍保持不可合并边界。
    assert result.source_counts == {"openalex": 1, "arxiv": 1, "dblp": 1, "semantic_scholar": 1, "tavily": 1}  # 验证来源级成功数量完整可观测。
    assert result.source_errors == {}  # 验证全部替身成功时不存在降级错误。


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
    )
    result = asyncio.run(coordinator.recall(_build_query_intent()))  # 执行不访问网络的核心双源协调流程。
    assert [paper.source for paper in result.papers] == ["openalex"]  # 验证失败来源不会丢弃主源已返回论文。
    assert result.source_counts == {"openalex": 1, "semantic_scholar": 0}  # 验证失败来源仍记录零结果统计。
    assert result.source_errors == {"semantic_scholar": "学术来源调用失败"}  # 验证调用方仅收到不含内部细节的降级摘要。


def test_coordinator_reports_unregistered_selected_source_without_raising() -> None:
    """路由选中但未注册适配器时协调器应记录配置降级而不抛出异常。"""
    coordinator = MultiSourceRecallCoordinator(  # 构造没有 OpenAlex 适配器注册的协调器。
        source_router=SourceRouter(Settings(_env_file=None)),  # 使用必然选择 OpenAlex 的隔离默认路由器。
        academic_adapters={},  # 故意留空以模拟应用装配遗漏。
    )
    result = asyncio.run(coordinator.recall(_build_query_intent()))  # 执行并触发未注册来源降级。
    assert result.papers == []  # 验证没有适配器时不会虚构论文结果。
    assert result.source_errors == {"openalex": "学术来源适配器未注册"}  # 验证返回稳定配置错误摘要。
