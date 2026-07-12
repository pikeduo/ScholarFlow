"""验证多源融合检索 HTTP 接口的成功、输入校验和内部故障边界。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。
from unittest.mock import patch  # 替换预期错误日志调用而不影响生产代码。

import pytest  # 提供测试夹具与异常断言工具。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证 HTTP 响应。

from backend.app.api.routes.search import get_multi_round_search_controller, get_multi_source_recall_coordinator, get_query_planning_service, get_search_run_state_store  # 覆盖生产协调器、控制器、查询规划与运行状态依赖。
from backend.app.main import app  # 导入待测 FastAPI 应用实例。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 构造稳定的多源响应结果。
from backend.app.models.multi_round_search import MultiRoundSearchResult  # 构造稳定的多轮搜索响应结果。
from backend.app.models.natural_search import QueryPlanningResult  # 构造带用量统计的查询规划结果。
from backend.app.models.paper import PaperRecord  # 构造融合论文响应数据。
from backend.app.models.source_routing import SourceRoutePlan  # 构造可审计来源路由计划。
from backend.app.models.query_intent import QueryIntent  # 构造自然语言入口规划结果。
from backend.app.models.search_event import SearchProgressEvent  # 构造 SSE 端点的安全进度事件。


class FakeMultiSourceRecallCoordinator:
    """为 HTTP 测试返回预设结果或模拟内部故障的协调器替身。"""

    def __init__(self, result: MultiSourceRecallResult | None = None, should_fail: bool = False) -> None:
        """保存无需网络的固定结果和失败开关。"""
        self._result = result  # 保存成功请求应返回的融合结果。
        self._should_fail = should_fail  # 保存是否模拟协调器无法形成稳定响应的异常。

    async def recall(self, _: object) -> MultiSourceRecallResult:
        """按测试配置返回结果或抛出内部错误。"""
        if self._should_fail:  # 仅在错误边界测试中模拟未预期故障。
            raise RuntimeError("模拟多源协调器故障")  # 让路由转换为稳定的 503 响应。
        if self._result is None:  # 防御测试替身遗漏成功结果的配置错误。
            raise AssertionError("测试替身未配置 MultiSourceRecallResult")  # 让测试配置问题立即可见。
        return self._result  # 返回预设的可序列化融合结果。


class FakeQueryPlanningService:
    """为自然语言接口返回固定英文 QueryIntent。"""

    async def plan(self, request: object) -> QueryPlanningResult:
        """返回不访问 DeepSeek 的固定查询计划及调用统计。"""
        intent = QueryIntent(original_query="中文查询", normalized_query="vision language model medical report generation", query_language="zh", research_topics=["vision-language model"], tasks=["medical report generation"], target_paper_count=20, source_recall_count=50)  # 构造英文检索计划。
        return QueryPlanningResult(query_intent=intent, model_name="deepseek-v4-flash", prompt_tokens=120, completion_tokens=80, duration_ms=450)  # 构造固定观测数据。


class FakeMultiRoundSearchController:
    """为多轮搜索 HTTP 测试返回预设结果或模拟内部故障的控制器替身。"""

    def __init__(self, result: MultiRoundSearchResult | None = None, should_fail: bool = False) -> None:
        """保存不访问来源、模型或网络的固定多轮结果和失败开关。"""
        self._result = result  # 保存成功请求应返回的多轮结果。
        self._should_fail = should_fail  # 保存是否模拟控制器未预期故障。

    async def run(self, _: QueryIntent, *, event_publisher: object | None = None) -> MultiRoundSearchResult:
        """按测试配置返回结果或触发安全 HTTP 错误边界。"""
        if self._should_fail:  # 仅在错误边界用例模拟内部错误。
            raise RuntimeError("模拟多轮控制器故障")  # 让路由转换为稳定 503 响应。
        if self._result is None:  # 防御测试替身遗漏成功结果配置。
            raise AssertionError("测试替身未配置 MultiRoundSearchResult")  # 让测试配置问题立即可见。
        if event_publisher is not None:  # SSE 端点传入发布器时模拟创建和完成事件。
            event_publisher.publish(SearchProgressEvent(run_id=self._result.run_state.run_id, event_type="run_created", node="search_run", current_round=0, progress=0.0, message="已创建搜索运行"))  # 发布不含查询正文的首个运行事件。
            event_publisher.publish(SearchProgressEvent(run_id=self._result.run_state.run_id, event_type="completed", node="compose_results", current_round=self._result.run_state.current_round, progress=1.0, message="搜索已完成", metrics={"final_paper_count": len(self._result.papers)}))  # 发布稳定完成事件。
        return self._result  # 返回预设的可序列化多轮响应。


class FakeSearchRunStateStore:
    """为运行状态读取接口返回固定状态或模拟存储错误的离线替身。"""

    def __init__(self, state: object | None = None, should_fail: bool = False) -> None:
        """保存无需 SQLite 的固定状态和失败开关。"""
        self._state = state  # 保存查询命中时应返回的运行状态。
        self._should_fail = should_fail  # 保存是否模拟存储读取故障。

    def save(self, _: object) -> None:
        """满足存储协议，本 HTTP 读取测试不需要写入。"""
        return None  # 保持替身无副作用。

    def get(self, _: str) -> object | None:
        """按测试配置返回状态、空值或触发安全错误边界。"""
        if self._should_fail:  # 仅在存储错误边界用例模拟异常。
            from backend.app.services.search_run_store import SearchRunStoreError  # 延迟导入稳定服务异常避免无关测试耦合。
            raise SearchRunStoreError("模拟状态存储故障")  # 让路由转换为不泄露内部细节的 503。
        return self._state  # 返回固定状态或不存在标识的空值。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供不触发应用生命周期且会清理多源依赖覆盖的本地 HTTP 客户端。"""
    client = TestClient(app)  # 构造本地 ASGI 客户端，避免测试触发 SQLite 初始化。
    yield client  # 交给测试用例发起不访问网络的 HTTP 请求。
    client.close()  # 释放测试客户端持有的本地资源。
    app.dependency_overrides.pop(get_multi_source_recall_coordinator, None)  # 防止替身污染后续测试。
    app.dependency_overrides.pop(get_multi_round_search_controller, None)  # 清理多轮控制器替身。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 清理搜索运行状态存储替身。
    app.dependency_overrides.pop(get_query_planning_service, None)  # 清理查询规划替身。
    get_multi_round_search_controller.cache_clear()  # 释放可能持有旧协调器的生产控制器缓存。


def _build_result() -> MultiSourceRecallResult:
    """构造包含融合论文、来源统计和降级信息的稳定多源响应。"""
    return MultiSourceRecallResult(  # 构造无需真实来源或网络的响应模型。
        route_plan=SourceRoutePlan(academic_sources=["openalex"], selection_reasons={"openalex": "固定主学术来源"}),  # 提供可审计的最小路由计划。
        papers=[PaperRecord(paper_id="W1", title="Fused Paper", source="openalex", rrf_score=0.02)],  # 提供一篇融合后的论文。
        source_counts={"openalex": 1},  # 提供来源级成功数量。
        raw_paper_count=1,  # 提供融合前原始论文数量。
        merged_paper_count=0,  # 提供无重复时的合并数量。
        work_family_count=1,  # 提供可识别版本族数量。
    )


def _build_multi_round_result() -> MultiRoundSearchResult:
    """构造含完成状态、停止原因和可编辑意图的稳定多轮响应。"""
    intent = QueryIntent(original_query="中文查询", normalized_query="vision language model medical report generation", query_language="zh", target_paper_count=20, source_recall_count=50)  # 构造自然语言入口将实际执行的意图。
    paper = PaperRecord(paper_id="W1", title="Fused Paper", source="openalex", rrf_score=0.02)  # 构造一篇无需外部服务的最终论文。
    from backend.app.models.search_run import SearchRunState  # 在测试辅助函数内导入运行状态避免无关用例依赖。
    run_state = SearchRunState(query_intent=intent, search_mode="standard", max_rounds=2, current_round=1, normalized_papers=[paper], candidate_ids=[paper.paper_id], final_papers=[paper], stop_reason="没有可执行的新查询", status="completed")  # 构造与控制器返回字段一致的最终运行状态。
    return MultiRoundSearchResult(run_state=run_state, query_intent=intent, papers=[paper], source_counts={"openalex": 1})  # 返回前端可消费的最小多轮搜索结果。


def _valid_query_payload() -> dict[str, object]:
    """构造满足 QueryIntent 最小契约的多源检索请求 JSON。"""
    return {  # 返回完整的最小请求正文。
        "original_query": "检索 Transformer 预测论文",  # 提供用户原始查询。
        "normalized_query": "Transformer forecasting",  # 提供可复现的规范化查询。
        "query_language": "mixed",  # 标记中英文混合查询。
        "research_topics": ["forecasting"],  # 提供至少一个可执行研究主题。
    }


def test_multi_source_search_endpoint_returns_fused_result(api_client: TestClient) -> None:
    """路由应返回协调器提供的融合论文、统计与来源计划。"""
    app.dependency_overrides[get_multi_source_recall_coordinator] = lambda: FakeMultiSourceRecallCoordinator(result=_build_result())  # 注入不访问网络的协调器替身。

    response = api_client.post("/api/v1/search/multi-source", json=_valid_query_payload())  # 提交合法 QueryIntent 请求。

    assert response.status_code == 200  # 验证成功请求返回固定状态码。
    payload = response.json()  # 解析公共 JSON 响应。
    assert payload["papers"][0]["paper_id"] == "W1"  # 验证返回融合论文列表。
    assert payload["papers"][0]["rrf_score"] == 0.02  # 验证 RRF 融合分数对前端可见。
    assert payload["raw_paper_count"] == 1  # 验证返回融合前召回统计。
    assert payload["route_plan"]["academic_sources"] == ["openalex"]  # 验证返回可审计来源计划。


def test_natural_search_endpoint_plans_query_before_recall(api_client: TestClient) -> None:
    """自然语言入口应先生成 QueryIntent，再复用多源协调器返回结果。"""
    app.dependency_overrides[get_query_planning_service] = lambda: FakeQueryPlanningService()  # 注入离线 Query Agent。
    app.dependency_overrides[get_multi_source_recall_coordinator] = lambda: FakeMultiSourceRecallCoordinator(result=_build_result())  # 注入离线协调器。

    response = api_client.post("/api/v1/search/natural", json={"query": "检索视觉语言模型在医学影像报告生成中的研究"})  # 提交自然语言请求。

    assert response.status_code == 200  # 验证新入口成功响应。
    payload = response.json()  # 解析自然入口公共响应。
    assert payload["papers"][0]["paper_id"] == "W1"  # 验证规划后进入既有检索链路。
    assert payload["query_planning_model_name"] == "deepseek-v4-flash"  # 验证规划模型统计附加到响应。
    assert payload["query_planning_prompt_tokens"] == 120  # 验证输入 Token 统计可供前端展示。
    assert payload["query_planning_completion_tokens"] == 80  # 验证输出 Token 统计可供前端展示。
    assert payload["query_planning_duration_ms"] == 450  # 验证规划耗时可供前端展示。


def test_multi_source_search_endpoint_rejects_invalid_query_intent(api_client: TestClient) -> None:
    """缺少 QueryIntent 必填字段的请求应在外部来源调用前返回 422。"""
    response = api_client.post("/api/v1/search/multi-source", json={"original_query": "缺少字段"})  # 故意遗漏规范化查询和语言。

    assert response.status_code == 422  # 验证无效请求不会进入协调器或任何外部适配器。


def test_multi_source_search_endpoint_hides_unexpected_coordinator_error(api_client: TestClient) -> None:
    """协调器出现未预期故障时路由应返回不泄露内部细节的稳定 503。"""
    app.dependency_overrides[get_multi_source_recall_coordinator] = lambda: FakeMultiSourceRecallCoordinator(should_fail=True)  # 注入会抛出内部异常的协调器替身。
    with patch("backend.app.api.routes.search.logger.exception") as log_exception:  # 拦截预期错误日志而不输出测试噪音。
        response = api_client.post("/api/v1/search/multi-source", json=_valid_query_payload())  # 提交合法请求触发协调器调用。

    assert response.status_code == 503  # 验证未预期错误被转换为服务不可用响应。
    assert response.json()["detail"] == "多源论文检索服务暂时不可用，请稍后重试"  # 验证不会泄露适配器或内部堆栈信息。
    log_exception.assert_called_once_with("多源检索接口调用失败")  # 验证完整堆栈仍写入受控日志。


def test_multi_round_search_endpoint_returns_run_state_and_stop_reason(api_client: TestClient) -> None:
    """多轮意图入口应返回累计论文、运行状态与可解释停止原因。"""
    app.dependency_overrides[get_multi_round_search_controller] = lambda: FakeMultiRoundSearchController(result=_build_multi_round_result())  # 注入离线多轮控制器替身。

    response = api_client.post("/api/v1/search/multi-round", json=_valid_query_payload())  # 提交合法的已编辑 QueryIntent。

    assert response.status_code == 200  # 验证多轮入口返回稳定成功状态。
    payload = response.json()  # 解析公共 JSON 响应。
    assert payload["papers"][0]["paper_id"] == "W1"  # 验证返回累计最终论文。
    assert payload["run_state"]["stop_reason"] == "没有可执行的新查询"  # 验证停止原因对前端可见。
    assert payload["query_intent"]["normalized_query"] == "vision language model medical report generation"  # 验证回显可编辑执行意图。


def test_natural_multi_round_search_attaches_query_planning_statistics(api_client: TestClient) -> None:
    """自然语言多轮入口应先规划意图，再回传规划模型、Token 与耗时统计。"""
    app.dependency_overrides[get_query_planning_service] = lambda: FakeQueryPlanningService()  # 注入不访问 DeepSeek 的规划替身。
    app.dependency_overrides[get_multi_round_search_controller] = lambda: FakeMultiRoundSearchController(result=_build_multi_round_result())  # 注入不访问来源的控制器替身。

    response = api_client.post("/api/v1/search/natural-multi-round", json={"query": "检索视觉语言模型在医学影像报告生成中的研究"})  # 提交自然语言搜索请求。

    assert response.status_code == 200  # 验证自然语言多轮入口返回稳定成功状态。
    payload = response.json()  # 解析可供前端消费的响应。
    assert payload["query_planning_model_name"] == "deepseek-v4-flash"  # 验证回显实际规划模型。
    assert payload["query_planning_prompt_tokens"] == 120 and payload["query_planning_completion_tokens"] == 80  # 验证回显 Query Agent Token 用量。
    assert payload["query_planning_duration_ms"] == 450  # 验证回显 Query Agent 耗时。
    assert payload["run_state"]["token_usage"] == 200  # 验证规划 Token 已纳入本次运行总量。


def test_multi_round_search_endpoint_hides_unexpected_controller_error(api_client: TestClient) -> None:
    """控制器出现未预期故障时多轮入口必须返回不泄露内部细节的 503。"""
    app.dependency_overrides[get_multi_round_search_controller] = lambda: FakeMultiRoundSearchController(should_fail=True)  # 注入会抛出内部错误的控制器替身。
    with patch("backend.app.api.routes.search.logger.exception") as log_exception:  # 拦截预期错误日志调用。
        response = api_client.post("/api/v1/search/multi-round", json=_valid_query_payload())  # 提交合法请求触发控制器错误。

    assert response.status_code == 503  # 验证内部错误转换为服务不可用。
    assert response.json()["detail"] == "多轮论文检索服务暂时不可用，请稍后重试"  # 验证不会泄露内部异常文本。
    log_exception.assert_called_once_with("多轮多源检索接口调用失败")  # 验证完整堆栈仍会写入受控日志。


def test_multi_round_event_endpoint_streams_safe_lifecycle_frames(api_client: TestClient) -> None:
    """SSE 端点应按 EventSource 帧格式输出创建和完成事件。"""
    app.dependency_overrides[get_multi_round_search_controller] = lambda: FakeMultiRoundSearchController(result=_build_multi_round_result())  # 注入会发布离线事件的控制器替身。

    response = api_client.post("/api/v1/search/multi-round/events", json=_valid_query_payload())  # 发起一次流式多轮搜索请求。

    assert response.status_code == 200  # 验证 SSE 请求成功建立。
    assert response.headers["content-type"].startswith("text/event-stream")  # 验证响应使用 EventSource 所需媒体类型。
    assert "event: run_created" in response.text and "event: completed" in response.text  # 验证创建和完成事件均已输出。
    assert "检索 Transformer 预测论文" not in response.text  # 验证流式事件不会包含用户完整查询正文。


def test_search_run_state_endpoint_returns_latest_snapshot_and_404_when_missing(api_client: TestClient) -> None:
    """运行状态接口应返回轻量快照，并将不存在 run_id 映射为稳定 404。"""
    expected_state = _build_multi_round_result().run_state  # 复用与多轮响应一致的完成状态。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeSearchRunStateStore(state=expected_state)  # 注入不访问 SQLite 的固定状态存储。

    response = api_client.get(f"/api/v1/search/runs/{expected_state.run_id}")  # 查询存在的运行标识。

    assert response.status_code == 200  # 验证最新轻量状态可以稳定读取。
    assert response.json()["run_id"] == expected_state.run_id  # 验证响应关联正确运行标识。
    assert response.json()["status"] == "completed"  # 验证终态可供轮询或 SSE 补偿消费。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeSearchRunStateStore()  # 切换为返回不存在状态的替身。
    missing_response = api_client.get("/api/v1/search/runs/missing-run")  # 查询未知运行标识。
    assert missing_response.status_code == 404 and missing_response.json()["detail"] == "搜索运行不存在"  # 验证不存在状态不会伪装为服务故障。


def test_production_coordinator_is_reused_within_process() -> None:
    """生产依赖应在同一进程复用协调器，避免每次请求重新加载本地模型。"""
    get_multi_source_recall_coordinator.cache_clear()  # 清除其他用例或导入过程可能留下的缓存实例。
    try:  # 确保测试结束不保留生产依赖对象。
        first_coordinator = get_multi_source_recall_coordinator()  # 首次构造全部懒加载适配器和排序服务。
        second_coordinator = get_multi_source_recall_coordinator()  # 再次获取应直接命中进程缓存。
        assert first_coordinator is second_coordinator  # 验证模型容器和来源限流状态不会按请求重建。
    finally:  # 清理缓存避免影响后续测试替身。
        get_multi_source_recall_coordinator.cache_clear()  # 释放当前测试创建的生产协调器引用。
