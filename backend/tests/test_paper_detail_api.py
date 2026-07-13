"""验证论文详情接口只读取已保存快照的成功、缺失和故障边界。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。
from unittest.mock import patch  # 拦截预期错误日志调用以保持测试输出干净。

import pytest  # 提供夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证 HTTP 响应。

from backend.app.api.routes.search import get_search_run_state_store  # 覆盖详情接口复用的 SQLite 存储依赖。
from backend.app.main import app  # 导入已装配版本化路由的 FastAPI 应用。
from backend.app.models.paper import PaperRecord  # 构造无需外部来源的规范化论文详情。
from backend.app.services.search_run_store import SearchRunStoreError  # 模拟安全存储边界错误。


class FakePaperDetailStore:
    """为详情接口提供不会访问 SQLite 或外部学术来源的读取替身。"""

    def __init__(self, paper: PaperRecord | None = None, should_fail: bool = False) -> None:
        """保存测试期望的详情记录或故障开关。"""
        self._paper = paper  # 保存命中论文或不存在标识对应的空值。
        self._should_fail = should_fail  # 保存是否模拟存储不可用。

    def get_paper(self, _: str) -> PaperRecord | None:
        """返回预设论文、空值或稳定存储错误。"""
        if self._should_fail:  # 仅在故障边界测试中触发。
            raise SearchRunStoreError("模拟论文详情存储故障")  # 让路由映射为不泄露内部细节的 503。
        return self._paper  # 返回固定详情或未知标识空值。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供不触发生命周期的本地 HTTP 客户端，并在结束后清理依赖覆盖。"""
    client = TestClient(app)  # 创建本地 ASGI 客户端而不访问真实网络。
    yield client  # 交给用例发起只读详情请求。
    client.close()  # 释放客户端资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 防止替身污染其他接口测试。


def _paper() -> PaperRecord:
    """构造包含标识符、摘要和证据的最小完整论文详情。"""
    return PaperRecord(paper_id="paper-detail-1", title="Evidence Grounded Retrieval", abstract="A saved abstract.", source="openalex", doi="10.1000/example", openalex_id="W123", references=["paper-reference-1"], constraint_evidence=["ETT benchmark"], recommendation_reason="覆盖了目标数据集")  # 使用统一领域契约生成可序列化详情。


def test_paper_detail_endpoint_returns_saved_paper_without_external_lookup(api_client: TestClient) -> None:
    """存在的论文标识应返回已保存 PaperRecord 的完整公开详情。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperDetailStore(paper=_paper())  # 注入只读固定快照替身。

    response = api_client.get("/api/v1/papers/detail?paper_id=paper-detail-1")  # 使用查询参数请求由搜索结果提供的内部论文标识。

    assert response.status_code == 200  # 验证详情读取成功。
    payload = response.json()  # 解析公开 JSON 响应。
    assert payload["paper_id"] == "paper-detail-1" and payload["doi"] == "10.1000/example"  # 验证返回已保存的规范化标识符。
    assert payload["constraint_evidence"] == ["ETT benchmark"]  # 验证详情保留已有核验证据而不重新生成。


def test_paper_detail_endpoint_accepts_source_identifier_with_slashes(api_client: TestClient) -> None:
    """来源 URL 型论文标识应通过查询参数完整进入 SQLite 详情读取边界。"""
    source_identifier_paper = _paper().model_copy(update={"paper_id": "https://openalex.org/W4387355843"})  # 构造会破坏旧路径分段的真实来源标识形态。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperDetailStore(paper=source_identifier_paper)  # 注入同一篇已保存来源论文。

    response = api_client.get("/api/v1/papers/detail?paper_id=https%3A%2F%2Fopenalex.org%2FW4387355843")  # 使用编码查询参数保留完整 URL 型标识。

    assert response.status_code == 200  # 验证路由不会将 URL 中的斜杠当作路径分段。
    assert response.json()["paper_id"] == "https://openalex.org/W4387355843"  # 验证后端读取并原样返回完整来源标识。


def test_paper_detail_endpoint_returns_404_for_unknown_saved_paper(api_client: TestClient) -> None:
    """未知论文标识不能伪造详情，应返回稳定不存在错误。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperDetailStore()  # 注入不命中的只读替身。

    response = api_client.get("/api/v1/papers/detail?paper_id=missing-paper")  # 请求未在任何保存结果中出现的标识。

    assert response.status_code == 404  # 验证未知详情不会被当成空成功响应。
    assert response.json()["detail"] == "论文详情不存在或尚未保存"  # 验证公共错误不泄露存储结构。


def test_paper_detail_endpoint_hides_storage_error(api_client: TestClient) -> None:
    """SQLite 或快照读取故障必须映射为不泄露内部信息的 503。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperDetailStore(should_fail=True)  # 注入会抛出安全服务错误的替身。
    with patch("backend.app.api.routes.papers.logger.exception") as log_exception:  # 拦截预期异常日志调用。
        response = api_client.get("/api/v1/papers/detail?paper_id=paper-detail-1")  # 触发详情读取错误边界。

    assert response.status_code == 503  # 验证故障转换为可重试服务不可用状态。
    assert response.json()["detail"] == "论文详情暂时不可用，请稍后重试"  # 验证客户端看不到 SQLite 或快照细节。
    log_exception.assert_called_once_with("论文详情读取接口失败：论文=%s", "paper-detail-1")  # 验证完整堆栈仍进入统一日志。
