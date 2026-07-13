"""验证搜索运行用量读取接口的成功、缺失和存储故障边界。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。

import pytest  # 提供测试夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证 HTTP 响应。

from backend.app.api.routes.search import get_search_run_state_store  # 覆盖生产 SQLite 存储装配。
from backend.app.main import app  # 导入待测 FastAPI 应用实例。
from backend.app.models.query_intent import QueryIntent  # 构造最小有效搜索运行意图。
from backend.app.models.search_run import SearchRunState  # 构造持久化运行快照。
from backend.app.services.search_run_store import SearchRunStoreError  # 模拟受控存储读取故障。


class FakeUsageStore:
    """为用量接口提供不访问 SQLite 的固定运行状态或故障替身。"""

    def __init__(self, state: SearchRunState | None = None, should_fail: bool = False) -> None:
        """保存应返回的状态和是否模拟存储不可用。

        参数：
            state：请求命中时返回的已保存搜索运行快照。
            should_fail：为真时在读取边界抛出受控存储异常。
        """
        self._state = state  # 保存无需真实数据库的固定快照。
        self._should_fail = should_fail  # 保存错误边界测试开关。

    def get(self, _: str) -> SearchRunState | None:
        """按测试配置返回快照、空值或受控存储异常。"""
        if self._should_fail:  # 仅在服务不可用测试中模拟 SQLite 访问失败。
            raise SearchRunStoreError("模拟搜索运行快照读取失败")  # 让路由映射为安全的 503 响应。
        return self._state  # 返回固定状态或模拟未命中运行标识。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供会清理依赖覆盖的本地 HTTP 测试客户端。"""
    client = TestClient(app)  # 构造不访问网络的本地 ASGI 客户端。
    yield client  # 将客户端交给测试用例发送 HTTP 请求。
    client.close()  # 释放客户端资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 防止替身污染其他接口测试。


def _build_state() -> SearchRunState:
    """构造含实际观测字段的已完成搜索运行快照。"""
    intent = QueryIntent(  # 构造满足运行状态契约的最小查询意图。
        original_query="查找时间序列预测论文",
        normalized_query="time series forecasting",
        query_language="zh",
    )
    return SearchRunState(  # 返回与 SQLite 反序列化对象字段一致的固定快照。
        run_id="run-usage-1",
        query_intent=intent,
        search_mode="standard",
        current_round=2,
        max_rounds=3,
        selected_sources=["openalex", "semantic_scholar"],
        api_call_count=5,
        token_usage=720,
        cost_usd=0.024,
        latency_ms=1860,
        cache_hits=3,
        stop_reason="已满足目标数量",
        status="completed",
    )


def test_usage_endpoint_returns_saved_run_metrics(api_client: TestClient) -> None:
    """已保存运行应返回同次快照的实际累计统计。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeUsageStore(_build_state())  # 注入无需 SQLite 的成功替身。

    response = api_client.get("/api/v1/usage/run-usage-1")  # 读取稳定运行标识的只读用量资源。

    assert response.status_code == 200  # 验证已保存运行可正常读取。
    payload = response.json()  # 解析公共 JSON 响应。
    assert payload["run_id"] == "run-usage-1"  # 验证响应保留关联标识。
    assert payload["api_call_count"] == 5  # 验证不重新估算 API 调用数。
    assert payload["token_usage"] == 720  # 验证保留实际 Token 统计。
    assert payload["selected_sources"] == ["openalex", "semantic_scholar"]  # 验证保留实际来源顺序。


def test_usage_endpoint_returns_404_when_run_is_missing(api_client: TestClient) -> None:
    """不存在的运行标识应返回明确资源不存在错误。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeUsageStore()  # 注入未命中状态的替身。

    response = api_client.get("/api/v1/usage/missing-run")  # 读取不存在的运行标识。

    assert response.status_code == 404  # 验证不会创建或伪造用量记录。
    assert response.json()["detail"] == "搜索运行不存在"  # 验证返回安全且稳定的公共错误。


def test_usage_endpoint_returns_503_when_store_fails(api_client: TestClient) -> None:
    """存储读取故障应记录后返回不泄露内部细节的服务错误。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeUsageStore(should_fail=True)  # 注入受控存储故障替身。

    response = api_client.get("/api/v1/usage/run-usage-1")  # 触发读取异常边界。

    assert response.status_code == 503  # 验证接口稳定降级而不是暴露堆栈。
    assert response.json()["detail"] == "搜索用量暂时不可用，请稍后重试"  # 验证公共错误可供前端直接展示。
