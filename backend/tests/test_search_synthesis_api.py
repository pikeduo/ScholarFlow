"""验证只读搜索综合报告的事实边界、正常输出与存储错误映射。"""

from collections.abc import Iterator  # 标注 pytest 夹具的生成器类型。

import pytest  # 声明本地 ASGI 客户端夹具。
from fastapi.testclient import TestClient  # 通过 FastAPI 应用验证公开接口契约。

from backend.app.api.routes.search import get_search_run_state_store  # 覆盖生产 SQLite 结果存储装配。
from backend.app.main import app  # 导入待测应用而不启动真实服务。
from backend.app.models.coverage import CoverageGap, CoverageReport  # 构造已保存的最终覆盖事实。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 构造完整多轮结果快照。
from backend.app.models.paper import PaperRecord  # 构造来源、关键词与核验状态不同的论文。
from backend.app.models.query_intent import QueryIntent  # 构造最小有效检索意图。
from backend.app.models.search_run import SearchRunState  # 构造与快照关联的完成状态。
from backend.app.services.search_run_store import SearchRunStoreError  # 模拟受控 SQLite 读取故障。


class _FakeResultStore:
    """为综合报告接口提供固定完成结果、空值或存储故障替身。"""

    def __init__(self, result: MultiRoundSearchResult | None = None, should_fail: bool = False) -> None:
        """保存测试期望的结果快照与故障开关。"""
        self._result = result  # 保持测试完全不读取真实 SQLite。
        self._should_fail = should_fail  # 控制受保护的持久化错误分支。

    def get_result(self, _: str) -> MultiRoundSearchResult | None:
        """按测试配置返回完成快照、空值或安全存储错误。"""
        if self._should_fail:  # 仅在故障用例模拟存储不可用。
            raise SearchRunStoreError("模拟搜索综合报告存储故障")  # 让路由映射为安全 503。
        return self._result  # 返回不涉及任何外部来源或模型调用的固定快照。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供在用例后清理依赖覆盖的本地 HTTP 客户端。"""
    client = TestClient(app)  # 使用内存 ASGI 调用验证路由边界。
    yield client  # 将客户端交由测试发起只读请求。
    client.close()  # 释放本地测试资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 避免替身污染其他测试。


def _build_result() -> MultiRoundSearchResult:
    """构造包含来源、年份、关键词和覆盖缺口的完成结果快照。"""
    intent = QueryIntent(original_query="检索 ETT 上的 Transformer 预测论文", normalized_query="Transformer ETT forecasting", query_language="mixed", datasets=["ETT"], methods=["Transformer"], target_paper_count=3)  # 保留缺口解释所需的结构化条件。
    papers = [
        PaperRecord(paper_id="paper-1", title="ETT Transformer Forecasting", source="openalex", year=2022, keywords=["Time Series", "Transformer"], constraint_status="satisfied", llm_relevance_score=0.9),  # 构造已满足关键约束的高相关论文。
        PaperRecord(paper_id="paper-2", title="Neural Forecasting", source="semantic_scholar", year=2024, keywords=["Time Series", "Benchmark"], constraint_status="uncertain", llm_relevance_score=0.7),  # 构造仍需人工确认的部分相关论文。
        PaperRecord(paper_id="paper-3", title="Survey of Forecasting", source="openalex", keywords=["Survey"], constraint_status="not_satisfied", llm_relevance_score=0.3),  # 构造未满足约束但可审计的保留候选。
    ]
    coverage = CoverageReport(target_count=3, high_relevance_count=1, partial_relevance_count=1, gaps=[CoverageGap(gap_type="dataset", constraint="ETT", severity=0.9, current_match_count=1, recommended_query_focus="ETT benchmark")], new_valid_count=1, marginal_gain=1 / 3, should_continue=False, stop_reason="已达到最大搜索轮次")  # 构造工作流已保存的最终覆盖结论。
    state = SearchRunState(run_id="run-synthesis-1", query_intent=intent, search_mode="standard", current_round=3, max_rounds=3, selected_sources=["openalex", "semantic_scholar"], final_papers=papers, coverage_report=coverage, stop_reason="已达到最大搜索轮次", status="completed")  # 构造与结果同次关联的终态快照。
    return MultiRoundSearchResult(run_state=state, query_intent=intent, papers=papers, source_counts={"openalex": 4, "semantic_scholar": 2}, coverage_report=coverage)  # 返回无需网络、模型或 PDF 的完整事实集合。


def test_synthesis_endpoint_returns_only_saved_result_facts(api_client: TestClient) -> None:
    """综合报告应稳定汇总同次快照中的数量、年份、来源、关键词与既有缺口。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: _FakeResultStore(_build_result())  # 注入不会访问 SQLite 的完成快照。

    response = api_client.get("/api/v1/search/runs/run-synthesis-1/synthesis")  # 请求同次运行的只读综合报告。

    assert response.status_code == 200  # 验证完成结果可生成报告。
    payload = response.json()  # 解析公开 JSON 契约。
    assert payload["run_id"] == "run-synthesis-1" and payload["final_paper_count"] == 3  # 验证报告关联同次运行并保留最终论文数量。
    assert payload["high_relevance_count"] == 1 and payload["partial_relevance_count"] == 1 and payload["not_satisfied_count"] == 1  # 验证三种核验状态不会混淆。
    assert (payload["year_start"], payload["year_end"]) == (2022, 2024)  # 验证仅由已保存且明确的年份形成范围。
    assert payload["sources"] == [{"source": "openalex", "recalled_count": 4, "final_paper_count": 2}, {"source": "semantic_scholar", "recalled_count": 2, "final_paper_count": 1}]  # 验证来源贡献使用已保存统计而非再次调用来源。
    assert payload["top_keywords"][0] == {"keyword": "Time Series", "paper_count": 2}  # 验证关键词只来自论文快照并按不同论文频次排序。
    assert payload["coverage_gaps"][0]["constraint"] == "ETT" and payload["follow_up_suggestions"]  # 验证建议只回显现有覆盖缺口。


def test_synthesis_endpoint_excludes_supplemental_discovery_source(api_client: TestClient) -> None:
    """Tavily 补充发现统计不能破坏仅接受学术来源的综合报告契约。"""
    result = _build_result()  # 复用包含可验证学术论文来源的完成结果快照。
    result.run_state.selected_sources.append("tavily")  # 模拟真实运行同时启用了网页补充发现来源。
    result.source_counts["tavily"] = 5  # 模拟补充发现被写入跨来源累计统计。
    app.dependency_overrides[get_search_run_state_store] = lambda: _FakeResultStore(result)  # 注入不访问 SQLite 或外部 API 的异常历史快照。

    response = api_client.get("/api/v1/search/runs/run-synthesis-1/synthesis")  # 验证只读报告可安全读取包含 Tavily 的历史运行。

    assert response.status_code == 200  # 验证不再因 Tavily 违反 PaperSource 枚举而返回 500。
    assert [source["source"] for source in response.json()["sources"]] == ["openalex", "semantic_scholar"]  # 验证报告仅展示可作为论文事实的学术来源。


def test_synthesis_endpoint_handles_missing_and_store_failure(api_client: TestClient) -> None:
    """未完成运行与存储故障应分别返回稳定 404 和不泄露细节的 503。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: _FakeResultStore()  # 构造未命中完成结果的场景。
    missing_response = api_client.get("/api/v1/search/runs/missing/synthesis")  # 请求不存在或尚未完成的运行。
    assert missing_response.status_code == 404 and missing_response.json()["detail"] == "搜索最终结果尚未就绪"  # 验证不会伪造空报告。

    app.dependency_overrides[get_search_run_state_store] = lambda: _FakeResultStore(should_fail=True)  # 切换到受控存储故障替身。
    failure_response = api_client.get("/api/v1/search/runs/run-synthesis-1/synthesis")  # 触发安全异常映射。
    assert failure_response.status_code == 503 and failure_response.json()["detail"] == "搜索综合报告暂时不可用，请稍后重试"  # 验证不泄露数据库或堆栈信息。
