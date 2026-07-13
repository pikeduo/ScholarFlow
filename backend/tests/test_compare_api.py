"""验证论文比较接口的成功、输入边界、未知论文和存储故障。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。
from unittest.mock import patch  # 拦截预期错误日志调用以避免测试噪音。

import pytest  # 提供测试夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证 HTTP 响应。

from backend.app.api.routes.papers import get_library_paper_repository  # 覆盖文献库快照回退依赖，避免测试读取真实 SQLite。
from backend.app.api.routes.search import get_search_run_state_store  # 覆盖比较接口复用的 SQLite 读取依赖。
from backend.app.main import app  # 导入已装配版本化路由的 FastAPI 应用。
from backend.app.models.paper import PaperRecord  # 构造无需来源或模型的已保存论文事实。
from backend.app.services.search_run_store import SearchRunStoreError  # 模拟安全存储边界错误。


class FakeComparisonStore:
    """为比较接口提供不会访问 SQLite、来源或 PDF 的批量论文替身。"""

    def __init__(self, papers: list[PaperRecord] | None = None, should_fail: bool = False) -> None:
        """保存固定论文集合或故障开关。"""
        self._papers = papers or []  # 保存比较请求应命中的论文。
        self._should_fail = should_fail  # 保存是否模拟持久化读取故障。

    def get_papers(self, paper_ids: object) -> list[PaperRecord]:
        """按自身保存顺序返回匹配论文，验证路由会负责契约顺序。"""
        if self._should_fail:  # 仅在故障边界用例模拟异常。
            raise SearchRunStoreError("模拟比较存储故障")  # 让路由映射为安全 503。
        requested_ids = set(paper_ids)  # 将测试请求标识转换为集合便于筛选。
        return [paper for paper in self._papers if paper.paper_id in requested_ids]  # 返回已有论文而不伪造缺失标识。


class FakeLibraryPaperRepository:
    """为比较接口提供不访问真实 SQLite 的收藏论文快照替身。"""

    def __init__(self, papers: list[PaperRecord] | None = None) -> None:
        """保存可供搜索快照缺失时回退的论文集合。"""
        self._papers = {paper.paper_id: paper for paper in papers or []}  # 按稳定论文标识建立测试索引。

    def find_paper(self, paper_id: str) -> PaperRecord | None:
        """返回指定标识的已收藏论文或空值。"""
        return self._papers.get(paper_id)  # 模拟生产仓储的精确本地读取。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供本地 HTTP 客户端并在用例结束后清理依赖覆盖。"""
    client = TestClient(app)  # 创建不会访问网络的 ASGI 客户端。
    yield client  # 交给用例调用比较接口。
    client.close()  # 释放客户端资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 防止替身污染其他 API 测试。
    app.dependency_overrides.pop(get_library_paper_repository, None)  # 防止文献库回退替身污染其他比较测试。


def _papers() -> list[PaperRecord]:
    """构造两篇带已有关键词、推荐理由和核验证据的论文事实。"""
    return [PaperRecord(paper_id="paper-1", title="Forecasting with Transformers", abstract="Uses ETT benchmark.", source="openalex", keywords=["Transformer", "ETT"], constraint_status="satisfied", constraint_evidence=["ETT benchmark"], recommendation_reason="满足数据集约束"), PaperRecord(paper_id="paper-2", title="Retrieval with Language Models", abstract="Evaluates academic retrieval.", source="semantic_scholar", keywords=["LLM", "retrieval"], constraint_status="uncertain")]  # 返回不依赖外部服务的规范化记录。


def test_compare_endpoint_returns_saved_facts_in_request_order(api_client: TestClient) -> None:
    """两篇已保存论文应按请求顺序返回事实字段和已有证据。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeComparisonStore(_papers())  # 注入固定已保存论文替身。
    app.dependency_overrides[get_library_paper_repository] = lambda: FakeLibraryPaperRepository()  # 保持搜索快照命中测试不访问真实文献库。

    response = api_client.post("/api/v1/compare", json={"paper_ids": ["paper-2", "paper-1"]})  # 使用反向顺序验证固定列顺序。

    assert response.status_code == 200  # 验证小集合比较成功。
    payload = response.json()  # 解析事实型比较响应。
    assert [item["paper_id"] for item in payload["items"]] == ["paper-2", "paper-1"]  # 验证响应严格保持用户选择顺序。
    assert payload["items"][1]["constraint_evidence"] == ["ETT benchmark"]  # 验证已有核验证据被原样复用。


def test_compare_endpoint_rejects_invalid_count_and_missing_paper(api_client: TestClient) -> None:
    """比较必须限制为两至五篇，且未知论文不能形成不可信对比列。"""
    count_response = api_client.post("/api/v1/compare", json={"paper_ids": ["paper-1"]})  # 提交数量不足的请求。
    assert count_response.status_code == 422  # 验证 Pydantic 在读取存储前拒绝无效数量。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeComparisonStore(_papers())  # 注入只包含两篇论文的替身。
    app.dependency_overrides[get_library_paper_repository] = lambda: FakeLibraryPaperRepository()  # 保持未知论文测试不访问真实文献库。
    missing_response = api_client.post("/api/v1/compare", json={"paper_ids": ["paper-1", "missing-paper"]})  # 请求其中一篇不存在的论文。
    assert missing_response.status_code == 404 and missing_response.json()["detail"] == "存在未保存的论文，无法比较"  # 验证不返回部分且误导的比较结果。


def test_compare_endpoint_hides_storage_error(api_client: TestClient) -> None:
    """快照读取失败时比较接口必须返回不泄露内部细节的 503。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeComparisonStore(should_fail=True)  # 注入会触发安全存储错误的替身。
    app.dependency_overrides[get_library_paper_repository] = lambda: FakeLibraryPaperRepository()  # 保持故障测试只覆盖搜索存储边界。
    with patch("backend.app.api.routes.compare.logger.exception") as log_exception:  # 拦截预期错误日志调用。
        response = api_client.post("/api/v1/compare", json={"paper_ids": ["paper-1", "paper-2"]})  # 触发存储故障边界。

    assert response.status_code == 503 and response.json()["detail"] == "论文比较数据暂时不可用，请稍后重试"  # 验证客户端不会收到存储实现细节。
    log_exception.assert_called_once_with("论文比较读取接口失败：数量=%s", 2)  # 验证完整堆栈仍记录到统一日志。


def test_compare_endpoint_falls_back_to_saved_library_papers(api_client: TestClient) -> None:
    """搜索快照缺失时，二至五篇收藏论文仍可形成事实型比较。"""
    papers = _papers()  # 构造两篇用户已收藏的稳定论文快照。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeComparisonStore()  # 模拟搜索运行快照已被清理。
    app.dependency_overrides[get_library_paper_repository] = lambda: FakeLibraryPaperRepository(papers)  # 注入文献库回退论文集合。

    response = api_client.post("/api/v1/compare", json={"paper_ids": ["paper-2", "paper-1"]})  # 使用文献库论文标识发起固定列比较。

    assert response.status_code == 200  # 验证仅依赖已收藏本地快照的比较成功。
    assert [item["paper_id"] for item in response.json()["items"]] == ["paper-2", "paper-1"]  # 验证回退后仍保持用户选择顺序。
