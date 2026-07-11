"""验证 OpenAlex 搜索 HTTP 接口的成功、校验和服务失败边界。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。

import pytest  # 提供测试夹具与异常断言工具。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证 HTTP 响应。

from backend.app.adapters.openalex import OpenAlexClientError  # 构造适配层已净化的失败场景。
from backend.app.api.routes.search import get_openalex_search_service  # 覆盖生产环境服务构造依赖。
from backend.app.main import app  # 导入待测 FastAPI 应用实例。
from backend.app.models.paper import Paper  # 构造稳定的论文响应数据。
from backend.app.models.search import SearchResult  # 构造服务层成功结果。


class FakeOpenAlexSearchService:
    """为 HTTP 测试返回预设结果或已净化错误的服务替身。

    参数：
        result：正常请求时返回的检索结果。
        error：需要模拟的 OpenAlex 服务异常；存在时优先抛出。
    """

    def __init__(self, result: SearchResult | None = None, error: OpenAlexClientError | None = None) -> None:
        """保存测试所需的固定结果或错误。"""
        self._result = result  # 保存无需网络的固定成功结果。
        self._error = error  # 保存已净化的外部服务错误。

    async def search(self, _: object) -> SearchResult:
        """按预设返回结果或抛出错误，不调用真实客户端。"""
        if self._error is not None:  # 优先模拟服务不可用边界。
            raise self._error  # 让路由转换为稳定的 HTTP 响应。
        if self._result is None:  # 防御测试替身未配置成功结果的编程错误。
            raise AssertionError("测试替身未配置 SearchResult")  # 让测试配置问题立即可见。
        return self._result  # 返回预设的可序列化服务结果。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供不触发应用生命周期且会清理依赖覆盖的本地 HTTP 客户端。"""
    client = TestClient(app)  # 构造本地 ASGI 客户端，避免测试触发 SQLite 初始化。
    yield client  # 交给测试用例发起不访问网络的 HTTP 请求。
    client.close()  # 释放测试客户端持有的本地资源。
    app.dependency_overrides.pop(get_openalex_search_service, None)  # 防止替身污染后续测试。


def test_openalex_search_endpoint_returns_service_result(api_client: TestClient) -> None:
    """路由应将服务层结果以稳定 JSON 响应返回。"""
    expected_result = SearchResult(  # 构造包含一篇论文的确定性检索结果。
        papers=[Paper(paper_id="W1", title="Forecasting Paper", source="openalex")],  # 提供最小合法论文记录。
        recalled_count=1,  # 声明原始召回数量。
        deduplicated_count=1,  # 声明去重后数量。
        filtered_count=0,  # 声明本地规则未移除论文。
    )
    app.dependency_overrides[get_openalex_search_service] = lambda: FakeOpenAlexSearchService(result=expected_result)  # 注入不访问网络的服务替身。
    response = api_client.post("/api/v1/search/openalex", json={"topic": ["forecasting"]})  # 提交合法结构化查询。
    payload = response.json()  # 解析响应便于断言公共字段。
    assert response.status_code == 200  # 验证成功请求返回固定状态码。
    assert payload["recalled_count"] == 1  # 验证保留服务层召回统计。
    assert payload["deduplicated_count"] == 1  # 验证保留服务层去重统计。
    assert payload["filtered_count"] == 0  # 验证返回本地规则过滤统计。
    assert payload["papers"][0]["paper_id"] == "W1"  # 验证论文列表按统一模型序列化。


def test_openalex_search_endpoint_rejects_invalid_query(api_client: TestClient) -> None:
    """不合法的年份区间应由 QuerySchema 在路由层返回 422。"""
    response = api_client.post(  # 提交起止年份倒置的请求。
        "/api/v1/search/openalex",
        json={"topic": ["forecasting"], "year_range": [2025, 2020]},
    )
    assert response.status_code == 422  # 验证无效输入不会进入外部搜索服务。


def test_openalex_search_endpoint_hides_client_error(api_client: TestClient) -> None:
    """适配层失败时路由应返回不含底层细节的服务不可用响应。"""
    failing_service = FakeOpenAlexSearchService(error=OpenAlexClientError("OpenAlex 网络请求失败"))  # 构造已净化的外部错误。
    app.dependency_overrides[get_openalex_search_service] = lambda: failing_service  # 注入会失败的服务替身。
    response = api_client.post("/api/v1/search/openalex", json={"topic": ["forecasting"]})  # 提交合法查询以触发服务调用。
    assert response.status_code == 503  # 验证外部服务失败转换为稳定 HTTP 状态。
    assert response.json()["detail"] == "OpenAlex 搜索服务暂时不可用，请稍后重试"  # 验证不会暴露网络或配置细节。
