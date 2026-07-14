"""验证受限引用图只返回已保存集合内的事实关系。"""

from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。

import pytest  # 提供测试夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证图接口。

from backend.app.api.routes.search import get_search_run_state_store  # 覆盖图接口复用的 SQLite 存储依赖。
from backend.app.main import app  # 导入已装配版本化路由的 FastAPI 应用。
from backend.app.models.paper import PaperRecord  # 构造已保存论文与引用事实。


class FakeGraphStore:
    """返回固定保存论文集合，不访问 SQLite、网络或 PDF。"""

    def get_papers(self, paper_ids: list[str]) -> list[PaperRecord]:
        """按自身顺序返回匹配论文，验证路由会重排节点。"""
        papers = [PaperRecord(paper_id="paper-1", title="Paper One", source="openalex", references=["paper-2", "external-paper"], work_family_id="family-1"), PaperRecord(paper_id="paper-2", title="Paper Two", source="semantic_scholar", work_family_id="family-1")]  # 构造一条内部引用、一条图外引用和一条版本族关系。
        return [paper for paper in papers if paper.paper_id in set(paper_ids)]  # 只返回已保存事实记录。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供本地 HTTP 客户端并清理依赖覆盖。"""
    client = TestClient(app)  # 创建不访问网络的 ASGI 客户端。
    yield client  # 交给用例读取图数据。
    client.close()  # 释放测试资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 防止替身污染其他用例。


def test_citation_graph_defaults_to_internal_citation_edges(api_client: TestClient) -> None:
    """默认图应按请求顺序返回节点，仅保留集合内真实引用。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeGraphStore()  # 注入固定保存论文替身。

    response = api_client.get("/api/v1/graph/citations?paper_ids=paper-2&paper_ids=paper-1&max_nodes=2")  # 使用反向顺序验证节点稳定性。

    assert response.status_code == 200  # 验证受限图读取成功。
    payload = response.json()  # 解析公共图响应。
    assert [node["paper_id"] for node in payload["nodes"]] == ["paper-2", "paper-1"]  # 验证节点按请求顺序排列。
    assert payload["nodes"][0]["work_family_id"] == "family-1"  # 验证前端可获得已保存的版本族聚合事实。
    assert {(edge["source_paper_id"], edge["target_paper_id"], edge["edge_type"]) for edge in payload["edges"]} == {("paper-1", "paper-2", "cites")}  # 验证默认只保留内部真实引用边。


def test_citation_graph_can_explicitly_include_version_family_edges(api_client: TestClient) -> None:
    """版本族关系仅在调用方显式选择后作为可区分事实边返回。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeGraphStore()  # 注入固定保存论文替身。

    response = api_client.get("/api/v1/graph/citations?paper_ids=paper-1&paper_ids=paper-2&edge_types=cites&edge_types=same_work")  # 显式请求两类已审计事实关系。

    assert response.status_code == 200  # 验证显式关系选择可正常读取。
    edge_types = {edge["edge_type"] for edge in response.json()["edges"]}  # 收集响应内的关系类型。
    assert edge_types == {"cites", "same_work"}  # 验证版本族不会被混同为真实引用。
