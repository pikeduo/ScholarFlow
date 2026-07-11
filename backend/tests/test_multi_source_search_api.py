"""验证多源融合检索 HTTP 接口的成功、输入校验和内部故障边界。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。
from unittest.mock import patch  # 替换预期错误日志调用而不影响生产代码。

import pytest  # 提供测试夹具与异常断言工具。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证 HTTP 响应。

from backend.app.api.routes.search import get_multi_source_recall_coordinator  # 覆盖生产环境多源协调器依赖。
from backend.app.main import app  # 导入待测 FastAPI 应用实例。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 构造稳定的多源响应结果。
from backend.app.models.paper import PaperRecord  # 构造融合论文响应数据。
from backend.app.models.source_routing import SourceRoutePlan  # 构造可审计来源路由计划。


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


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供不触发应用生命周期且会清理多源依赖覆盖的本地 HTTP 客户端。"""
    client = TestClient(app)  # 构造本地 ASGI 客户端，避免测试触发 SQLite 初始化。
    yield client  # 交给测试用例发起不访问网络的 HTTP 请求。
    client.close()  # 释放测试客户端持有的本地资源。
    app.dependency_overrides.pop(get_multi_source_recall_coordinator, None)  # 防止替身污染后续测试。


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


def test_production_coordinator_is_reused_within_process() -> None:
    """生产依赖应在同一进程复用协调器，避免每次请求重新加载本地模型。"""
    get_multi_source_recall_coordinator.cache_clear()  # 清除其他用例或导入过程可能留下的缓存实例。
    try:  # 确保测试结束不保留生产依赖对象。
        first_coordinator = get_multi_source_recall_coordinator()  # 首次构造全部懒加载适配器和排序服务。
        second_coordinator = get_multi_source_recall_coordinator()  # 再次获取应直接命中进程缓存。
        assert first_coordinator is second_coordinator  # 验证模型容器和来源限流状态不会按请求重建。
    finally:  # 清理缓存避免影响后续测试替身。
        get_multi_source_recall_coordinator.cache_clear()  # 释放当前测试创建的生产协调器引用。
