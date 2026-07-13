"""验证搜索运行历史列表与终态清理接口的安全边界。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。
from datetime import datetime, timezone  # 构造稳定 UTC 历史索引时间。

import pytest  # 提供测试夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证 HTTP 响应。

from backend.app.api.routes.search import get_search_run_state_store  # 覆盖生产 SQLite 运行存储装配。
from backend.app.main import app  # 导入待测 FastAPI 应用实例。
from backend.app.models.search_run_history import SearchRunHistoryItem  # 构造不含查询正文的历史索引。
from backend.app.services.search_run_store import SearchRunStoreError  # 模拟受控存储故障。


class FakeHistoryStore:
    """为历史读取与终态清理接口提供无 SQLite 的受控替身。"""

    def __init__(self, items: list[SearchRunHistoryItem] | None = None, deletion_result: str = "deleted", should_fail: bool = False) -> None:
        """保存历史索引、删除结果和可选存储故障开关。

        参数：
            items：历史读取时应返回的安全运行索引。
            deletion_result：删除请求返回的受控状态文本。
            should_fail：为真时所有存储操作抛出受控异常。
        """
        self._items = items or []  # 保存不含真实用户数据的固定历史索引。
        self._deletion_result = deletion_result  # 保存删除边界用例需要的返回状态。
        self._should_fail = should_fail  # 保存模拟 SQLite 不可用的测试开关。
        self.deleted_run_ids: list[str] = []  # 记录删除请求的运行标识供断言。

    def list_history(self, _: int) -> list[SearchRunHistoryItem]:
        """返回固定历史索引或模拟安全存储错误。"""
        if self._should_fail:  # 仅在服务不可用测试中触发。
            raise SearchRunStoreError("模拟历史读取故障")  # 让 API 映射为不泄露细节的 503。
        return self._items  # 返回预设的最小历史元数据。

    def delete_terminal_run(self, run_id: str) -> str:
        """记录清理请求并返回固定删除边界结果。"""
        if self._should_fail:  # 复用错误开关覆盖清理存储边界。
            raise SearchRunStoreError("模拟历史清理故障")  # 让 API 映射为稳定 503。
        self.deleted_run_ids.append(run_id)  # 记录前端明确请求清理的运行标识。
        return self._deletion_result  # 返回 deleted、missing 或 active。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供会在测试结束后清理依赖覆盖的本地 HTTP 客户端。"""
    client = TestClient(app)  # 构造不访问网络或用户 SQLite 的 ASGI 客户端。
    yield client  # 交给测试用例发送运行历史请求。
    client.close()  # 释放客户端资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 防止替身污染其他接口测试。


def _history_item() -> SearchRunHistoryItem:
    """构造不含查询正文、可恢复且可清理的完成运行索引。"""
    timestamp = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)  # 固定 UTC 时间避免测试依赖当前时钟。
    return SearchRunHistoryItem(  # 返回符合公共历史契约的最小索引项。
        run_id="run-history-1",
        status="completed",
        current_round=2,
        max_rounds=3,
        selected_sources=["openalex", "semantic_scholar"],
        stop_reason="已满足目标数量",
        result_ready=True,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_history_endpoint_returns_safe_run_index(api_client: TestClient) -> None:
    """历史列表应只返回运行元数据而不包含查询正文或论文内容。"""
    store = FakeHistoryStore(items=[_history_item()])  # 注入无需 SQLite 的固定历史替身。
    app.dependency_overrides[get_search_run_state_store] = lambda: store  # 装配替身到历史读取依赖。

    response = api_client.get("/api/v1/search/runs?limit=10")  # 读取最近十条本地运行索引。

    assert response.status_code == 200  # 验证历史读取成功。
    payload = response.json()  # 解析公共 JSON 响应。
    assert payload["limit"] == 10  # 验证响应保留实际数量上限。
    assert payload["items"][0]["run_id"] == "run-history-1"  # 验证恢复入口所需标识存在。
    assert "original_query" not in payload["items"][0]  # 验证历史索引不泄露查询正文。
    assert "papers" not in payload["items"][0]  # 验证历史索引不携带论文集合。


def test_delete_history_endpoint_accepts_terminal_and_rejects_missing_or_active(api_client: TestClient) -> None:
    """清理接口应删除终态运行，并对不存在或运行中状态返回明确边界。"""
    deleted_store = FakeHistoryStore(deletion_result="deleted")  # 构造终态删除成功替身。
    app.dependency_overrides[get_search_run_state_store] = lambda: deleted_store  # 注入成功删除替身。

    deleted_response = api_client.delete("/api/v1/search/runs/run-history-1")  # 显式清理终态运行。

    assert deleted_response.status_code == 204  # 验证成功删除返回无正文状态码。
    assert deleted_store.deleted_run_ids == ["run-history-1"]  # 验证仅删除用户指定的运行标识。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeHistoryStore(deletion_result="missing")  # 切换为不存在运行替身。

    missing_response = api_client.delete("/api/v1/search/runs/missing-run")  # 尝试清理不存在运行。

    assert missing_response.status_code == 404  # 验证过期历史条目可被前端明确识别。
    assert missing_response.json()["detail"] == "搜索运行不存在"  # 验证公共错误不泄露存储细节。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeHistoryStore(deletion_result="active")  # 切换为运行中替身。

    active_response = api_client.delete("/api/v1/search/runs/running-run")  # 尝试清理仍在后台执行的运行。

    assert active_response.status_code == 409  # 验证接口不会隐式取消运行中工作流。
    assert active_response.json()["detail"] == "搜索运行仍在执行，暂不能清理"  # 验证前端可安全展示冲突原因。


def test_history_endpoint_returns_503_when_store_fails(api_client: TestClient) -> None:
    """历史读取与清理的存储故障应映射为安全服务错误。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeHistoryStore(should_fail=True)  # 注入受控存储故障替身。

    list_response = api_client.get("/api/v1/search/runs")  # 触发历史读取存储异常。
    delete_response = api_client.delete("/api/v1/search/runs/run-history-1")  # 触发历史清理存储异常。

    assert list_response.status_code == 503  # 验证读取错误不会暴露 SQLite 细节。
    assert delete_response.status_code == 503  # 验证清理错误不会留下不明确响应。
