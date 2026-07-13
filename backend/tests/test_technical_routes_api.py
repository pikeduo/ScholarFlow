"""验证技术路线接口只基于已保存论文关键词读取事实。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。
from unittest.mock import patch  # 拦截预期异常日志调用以保持测试输出干净。

import pytest  # 提供夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证技术路线接口。

from backend.app.api.routes.search import get_search_run_state_store  # 覆盖技术路线接口复用的 SQLite 存储依赖。
from backend.app.main import app  # 导入已装配版本化路由的 FastAPI 应用。
from backend.app.models.paper import PaperRecord  # 构造已保存论文关键词事实。
from backend.app.services.search_run_store import SearchRunStoreError  # 模拟安全存储边界错误。


class FakeTechnicalRouteStore:
    """返回固定已保存论文或抛出存储故障，不访问 SQLite、模型或外部来源。"""

    def __init__(self, papers: list[PaperRecord], should_fail: bool = False) -> None:
        """保存固定论文集合和受控故障开关。"""
        self._papers = papers  # 保存当前可读取的已持久化论文事实。
        self._should_fail = should_fail  # 控制是否模拟 SQLite 或快照读取故障。

    def get_papers(self, paper_ids: list[str]) -> list[PaperRecord]:
        """返回匹配论文，故障时抛出稳定存储异常。"""
        if self._should_fail:  # 仅在故障边界用例触发。
            raise SearchRunStoreError("模拟技术路线存储故障")  # 让路由映射为不泄露内部细节的 503。
        return [paper for paper in self._papers if paper.paper_id in set(paper_ids)]  # 模拟只从保存集合读取指定论文。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供本地 ASGI 客户端，并在测试结束后清理依赖覆盖。"""
    client = TestClient(app)  # 创建不访问真实网络的本地 HTTP 客户端。
    yield client  # 交给测试用例发起技术路线读取请求。
    client.close()  # 释放测试资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 防止存储替身污染其他接口用例。


def _papers() -> list[PaperRecord]:
    """构造具有共享和独有关键词的最小已保存论文集合。"""
    return [  # 返回可验证路线排序和成员顺序的事实记录。
        PaperRecord(paper_id="paper-1", title="Transformer Forecasting", source="openalex", keywords=["Transformer", "ETT"]),  # 提供两个关键词证据。
        PaperRecord(paper_id="paper-2", title="Transformer Benchmark", source="semantic_scholar", keywords=["Transformer"]),  # 与第一篇共享 Transformer 关键词。
    ]


def test_technical_routes_endpoint_returns_keyword_fact_routes_in_request_order(api_client: TestClient) -> None:
    """两篇已保存论文应生成由关键词事实支持的路线，并保持请求论文顺序。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeTechnicalRouteStore(_papers())  # 注入只读固定快照替身。

    response = api_client.get("/api/v1/routes?paper_ids=paper-2&paper_ids=paper-1")  # 使用反向请求顺序验证路线成员按用户集合顺序排列。

    assert response.status_code == 200  # 验证技术路线只读接口成功。
    payload = response.json()  # 解析公共技术路线响应。
    assert payload["routes"][0]["name"] == "Transformer"  # 验证共享关键词优先按覆盖论文数量排序。
    assert payload["routes"][0]["paper_ids"] == ["paper-2", "paper-1"]  # 验证路线成员保持用户请求顺序。
    assert payload["routes"][0]["evidence"] == ["Transformer"]  # 验证路线证据只引用已保存关键词事实。


def test_technical_routes_endpoint_rejects_missing_saved_paper(api_client: TestClient) -> None:
    """请求集合中存在未保存论文时不得返回部分路线。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeTechnicalRouteStore(_papers())  # 注入只包含两篇已保存论文的替身。

    response = api_client.get("/api/v1/routes?paper_ids=paper-1&paper_ids=missing-paper")  # 请求一篇不存在于保存集合的论文。

    assert response.status_code == 404  # 验证缺失论文映射为稳定资源错误。
    assert response.json()["detail"] == "存在未保存的论文，无法生成技术路线"  # 验证公共错误不泄露存储细节。


def test_technical_routes_endpoint_hides_storage_error(api_client: TestClient) -> None:
    """保存快照读取失败必须映射为安全的可重试服务错误。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeTechnicalRouteStore(_papers(), should_fail=True)  # 注入会抛出存储错误的替身。
    with patch("backend.app.api.routes.routes.logger.exception") as log_exception:  # 拦截预期完整堆栈日志调用。
        response = api_client.get("/api/v1/routes?paper_ids=paper-1")  # 触发技术路线读取故障边界。

    assert response.status_code == 503  # 验证存储故障不会泄露为 500。
    assert response.json()["detail"] == "技术路线数据暂时不可用，请稍后重试"  # 验证客户端获得稳定可重试提示。
    log_exception.assert_called_once_with("技术路线读取接口失败：数量=%s", 1)  # 验证服务端仍保留安全堆栈记录。
