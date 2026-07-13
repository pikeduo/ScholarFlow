"""验证已保存搜索结果筛选、排序和分页读取接口的稳定边界。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。

import pytest  # 提供测试夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证 HTTP 响应。

from backend.app.api.routes.search import get_search_run_state_store  # 覆盖生产 SQLite 结果存储装配。
from backend.app.main import app  # 导入待测 FastAPI 应用实例。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 构造已完成多轮结果快照。
from backend.app.models.paper import PaperRecord  # 构造可筛选与排序的论文事实。
from backend.app.models.query_intent import QueryIntent  # 构造最小有效查询意图。
from backend.app.models.search_run import SearchRunState  # 构造与结果关联的完成运行状态。
from backend.app.services.search_run_store import SearchRunStoreError  # 模拟受控持久化读取故障。


class FakeResultStore:
    """为结果页接口提供固定完成结果、未命中或存储故障替身。"""

    def __init__(self, result: MultiRoundSearchResult | None = None, should_fail: bool = False) -> None:
        """保存测试应返回的完整结果快照及错误开关。

        参数：
            result：命中运行标识时返回的同次最终结果。
            should_fail：为真时模拟 SQLite 读取边界故障。
        """
        self._result = result  # 保存不访问真实 SQLite 的固定结果。
        self._should_fail = should_fail  # 保存服务不可用测试开关。

    def get_result(self, _: str) -> MultiRoundSearchResult | None:
        """按配置返回完成结果、空值或受控存储错误。"""
        if self._should_fail:  # 仅在错误边界测试中模拟安全存储异常。
            raise SearchRunStoreError("模拟结果快照读取失败")  # 让路由映射为稳定 503 响应。
        return self._result  # 返回固定快照或尚未完成的空值。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供会在用例后清理依赖覆盖的本地 HTTP 客户端。"""
    client = TestClient(app)  # 构造不访问网络或用户 SQLite 的 ASGI 客户端。
    yield client  # 将客户端交给测试用例发起只读请求。
    client.close()  # 释放本地客户端资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 防止替身污染其他接口测试。


def _build_result() -> MultiRoundSearchResult:
    """构造三篇具有来源、年份、核验状态和引用量差异的完成结果。"""
    intent = QueryIntent(  # 构造完成运行所需的最小结构化检索意图。
        original_query="查找时间序列预测论文",
        normalized_query="time series forecasting",
        query_language="zh",
    )
    papers = [  # 保留默认相关性顺序，用于验证 relevance 排序不改写它。
        PaperRecord(paper_id="paper-1", title="Highest relevance", source="openalex", year=2021, citation_count=5, constraint_status="satisfied"),
        PaperRecord(paper_id="paper-2", title="Newest paper", source="semantic_scholar", year=2024, citation_count=20, constraint_status="uncertain"),
        PaperRecord(paper_id="paper-3", title="Most cited", source="openalex", year=2019, citation_count=80, constraint_status="satisfied"),
    ]
    state = SearchRunState(  # 构造与完成结果持久化一致的运行状态。
        run_id="run-page-1",
        query_intent=intent,
        search_mode="standard",
        current_round=1,
        max_rounds=2,
        final_papers=papers,
        status="completed",
    )
    return MultiRoundSearchResult(run_state=state, query_intent=intent, papers=papers)  # 返回无需真实来源的固定快照。


def test_result_page_endpoint_filters_sorts_and_paginates_saved_papers(api_client: TestClient) -> None:
    """接口应只基于同次完成快照筛选、按年份排序并返回页码元数据。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeResultStore(_build_result())  # 注入不访问 SQLite 的完成结果替身。

    response = api_client.get("/api/v1/search/runs/run-page-1/papers?source=openalex&relevance=satisfied&year_start=2020&sort=year_desc&page=1&page_size=1")  # 请求组合筛选、展示排序和分页。

    assert response.status_code == 200  # 验证已保存完成结果可被稳定读取。
    payload = response.json()  # 解析公共分页响应。
    assert payload["run_id"] == "run-page-1"  # 验证结果仍关联同次搜索运行。
    assert payload["total"] == 1 and payload["total_pages"] == 1  # 验证筛选后总数和页数由服务端计算。
    assert [paper["paper_id"] for paper in payload["items"]] == ["paper-1"]  # 验证来源、核验状态和年份下界均已生效。

    citation_response = api_client.get("/api/v1/search/runs/run-page-1/papers?sort=citation_desc")  # 请求引用量展示排序。

    assert [paper["paper_id"] for paper in citation_response.json()["items"]] == ["paper-3", "paper-2", "paper-1"]  # 验证排序不触发重新检索且顺序确定。


def test_result_page_endpoint_rejects_invalid_range_and_missing_result(api_client: TestClient) -> None:
    """非法年份范围和未完成运行应分别返回稳定 422 与 404。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeResultStore(_build_result())  # 注入完成结果以验证参数在读取前被拒绝。

    invalid_response = api_client.get("/api/v1/search/runs/run-page-1/papers?year_start=2025&year_end=2020")  # 请求倒置年份范围。

    assert invalid_response.status_code == 422  # 验证跨字段年份边界稳定拒绝。
    assert invalid_response.json()["detail"] == "起始年份不能晚于结束年份"  # 验证前端可直接展示公共参数错误。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeResultStore()  # 切换为尚无完成结果的替身。

    missing_response = api_client.get("/api/v1/search/runs/missing-run/papers")  # 请求未知或未完成运行的结果页。

    assert missing_response.status_code == 404  # 验证不会伪造空分页集合。
    assert missing_response.json()["detail"] == "搜索最终结果尚未就绪"  # 验证语义与完整结果读取端点一致。


def test_result_page_endpoint_returns_503_when_store_fails(api_client: TestClient) -> None:
    """存储故障应记录并返回不含 SQLite 细节的稳定服务错误。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeResultStore(should_fail=True)  # 注入受控持久化故障替身。

    response = api_client.get("/api/v1/search/runs/run-page-1/papers")  # 触发结果快照读取异常边界。

    assert response.status_code == 503  # 验证路由不泄露堆栈或数据库路径。
    assert response.json()["detail"] == "搜索结果暂时不可用，请稍后重试"  # 验证前端可消费稳定公共错误。
