"""验证个人文献库 SQLite 闭环、去重规则和稳定 HTTP 错误边界。"""

from collections.abc import Iterator  # 标注测试夹具的会话生成器。
from unittest.mock import patch  # 隔离预期数据库错误日志。

import pytest  # 提供测试夹具。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证完整 API。
from sqlalchemy import create_engine  # 创建隔离内存 SQLite 引擎。
from sqlalchemy.exc import SQLAlchemyError  # 模拟持久化故障。
from sqlalchemy.orm import Session, sessionmaker  # 创建测试会话工厂。
from sqlalchemy.pool import StaticPool  # 让多线程测试客户端共享同一内存数据库。

from backend.app.api.routes.library import get_database_session, get_library_paper_indexer, get_library_semantic_searcher, get_library_service  # 覆盖生产数据库、模型和服务依赖。
from backend.app.models.library import LibrarySemanticSearchResult  # 构造不加载真实模型的自然语言检索响应。
from backend.app.services.library_vector_index import LibraryVectorIndexResult  # 构造不加载真实模型的延迟索引替身。
from backend.app.main import app  # 导入已装配文献库路由的 FastAPI 应用。
from backend.app.models.paper import PaperRecord  # 构造去重优先级测试论文。
from backend.app.repositories.database import Base  # 使用生产元数据创建测试表。
from backend.app.repositories.library import LibraryItemRow, build_library_identity_key  # 验证 ORM 注册和身份去重优先级。


@pytest.fixture
def library_client() -> Iterator[TestClient]:
    """提供共享内存 SQLite 且不会触碰真实业务数据的 API 客户端。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)  # 创建线程安全的单连接内存库。
    _ = LibraryItemRow  # 明确导入用于注册 SQLAlchemy 元数据。
    Base.metadata.create_all(bind=engine)  # 只在内存引擎创建业务表。
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)  # 创建隔离会话工厂。

    def override_session() -> Iterator[Session]:
        """为每个测试请求提供隔离会话。"""
        session = testing_session()  # 创建请求级测试会话。
        try:  # 将会话生命周期绑定到请求。
            yield session  # 交给真实仓储和服务层使用。
        finally:  # 确保请求结束释放会话。
            session.close()  # 关闭测试连接句柄。

    app.dependency_overrides[get_database_session] = override_session  # 替换真实 SQLite 会话。
    app.dependency_overrides[get_library_paper_indexer] = lambda: _NoopLibraryPaperIndexer()  # 阻止 API 单测加载或下载 BGE-M3 模型。
    app.dependency_overrides[get_library_semantic_searcher] = lambda: _NoopLibrarySemanticSearcher()  # 阻止 API 单测读取真实 FAISS 索引。
    client = TestClient(app)  # 构造不启动真实服务的本地客户端。
    yield client  # 交给用例验证完整路由闭环。
    client.close()  # 释放 ASGI 客户端资源。
    app.dependency_overrides.pop(get_database_session, None)  # 清理会话覆盖防止污染其他测试。
    app.dependency_overrides.pop(get_library_paper_indexer, None)  # 清理模型索引器覆盖防止污染其他测试。
    app.dependency_overrides.pop(get_library_semantic_searcher, None)  # 清理语义检索器覆盖防止污染其他测试。
    app.dependency_overrides.pop(get_library_service, None)  # 清理可能由异常用例设置的服务覆盖。
    Base.metadata.drop_all(bind=engine)  # 删除内存表以隔离下一用例。
    engine.dispose()  # 释放内存数据库连接。


class _NoopLibraryPaperIndexer:
    """在 API 单测中替代真实模型和 FAISS 的无操作延迟索引器。"""

    index_calls = 0  # 记录同步兼容入口，验证收藏路径不会调用它。
    async_index_calls = 0  # 记录首次语义检索前的异步索引调用次数。

    def index(self, paper: PaperRecord, metadata_repository: object) -> LibraryVectorIndexResult:
        """不加载模型、不写入索引，仅返回可解释测试降级结果。"""
        type(self).index_calls += 1  # 记录同步入口是否被错误用于收藏路径。
        _ = paper, metadata_repository  # 明确测试替身不会读取论文内容或 SQLite 映射。
        return LibraryVectorIndexResult(indexed=False, vector_id=None, reason="测试未启用语义索引")  # 保持收藏 API 响应契约不变。

    async def index_async(self, paper: PaperRecord, metadata_repository: object) -> LibraryVectorIndexResult:
        """记录首次语义检索前的延迟索引调用，且不加载真实 BGE 或 FAISS。"""
        type(self).async_index_calls += 1  # 记录异步索引只在语义检索请求时发生。
        _ = paper, metadata_repository  # 明确测试替身不会读取论文内容或 SQLite 映射。
        return LibraryVectorIndexResult(indexed=False, vector_id=None, reason="测试未启用语义索引")  # 保持后续语义检索响应契约不变。


class _NoopLibrarySemanticSearcher:
    """在 API 单测中替代真实 BGE 和 FAISS 的自然语言检索器。"""

    async def search(self, query: str, items: list[object], metadata_repository: object, top_k: int) -> LibrarySemanticSearchResult:
        """验证路由已完成结构化筛选后调用检索器，并返回稳定空结果。"""
        _ = query, items, metadata_repository, top_k  # 测试替身不读取查询或数据库内容。
        return LibrarySemanticSearchResult()  # 维持 API 响应契约且不触发模型加载。


def _paper_payload(paper_id: str = "paper-1", doi: str = "10.1000/library-test", title: str = "Evidence-Grounded Retrieval", keywords: list[str] | None = None) -> dict[str, object]:
    """构造满足 PaperRecord 契约的最小论文 JSON。"""
    return {"paper_id": paper_id, "title": title, "abstract": "A retrieval study.", "authors": [{"name": "Ada Lovelace"}], "year": 2025, "doi": doi, "source": "manual", "keywords": keywords or []}  # 返回可直接提交的 JSON 对象。


def test_library_identity_uses_doi_then_source_identifiers() -> None:
    """收藏去重键应优先使用 DOI，并在缺失时依次回退到来源标识。"""
    doi_paper = PaperRecord(paper_id="internal-1", title="DOI Paper", source="manual", doi="https://doi.org/10.1000/EXAMPLE", arxiv_id="2501.00001")  # 同时提供 DOI 和 arXiv ID。
    arxiv_paper = PaperRecord(paper_id="internal-2", title="arXiv Paper", source="manual", arxiv_id="2501.00002")  # 仅提供 arXiv 来源标识。
    internal_paper = PaperRecord(paper_id="INTERNAL-3", title="Internal Paper", source="manual")  # 仅保留必填内部 ID。

    assert build_library_identity_key(doi_paper) == "doi:10.1000/example"  # 验证 DOI URL 规范化且优先于 arXiv ID。
    assert build_library_identity_key(arxiv_paper) == "arxiv:2501.00002"  # 验证 DOI 缺失时回退 arXiv ID。
    assert build_library_identity_key(internal_paper) == "paper:internal-3"  # 验证全部来源标识缺失时回退内部 ID。


def test_library_starts_empty_and_deduplicates_doi_saves(library_client: TestClient) -> None:
    """空文献库应可查询，重复 DOI 收藏应刷新快照并合并关键词。"""
    empty_response = library_client.get("/api/v1/library/items")  # 查询尚未保存论文的文献库。
    first_response = library_client.post("/api/v1/library/items", json={"paper": _paper_payload(), "keywords": ["LLM", "检索"], "note": "首次收藏"})  # 创建首条收藏。
    duplicate_response = library_client.post("/api/v1/library/items", json={"paper": _paper_payload(paper_id="other-source-id", doi="https://doi.org/10.1000/LIBRARY-TEST", title="Updated Retrieval Metadata"), "tags": ["llm", "证据"]})  # 旧标签字段也应兼容同 DOI 重复收藏。
    list_response = library_client.get("/api/v1/library/items")  # 查询去重后的完整集合。

    assert empty_response.status_code == 200 and empty_response.json() == {"items": [], "total": 0, "page": 1, "page_size": 10, "total_pages": 1, "keyword_facets": []}  # 验证空结果边界和分页元数据稳定。
    assert first_response.status_code == 200 and first_response.json()["created"] is True  # 验证首次保存实际创建记录。
    assert duplicate_response.status_code == 200 and duplicate_response.json()["created"] is False  # 验证规范化 DOI 命中已有记录。
    assert duplicate_response.json()["item"]["keywords"] == ["LLM", "检索", "证据"]  # 验证关键词大小写无关合并并保留首次形式。
    assert duplicate_response.json()["item"]["tags"] == ["LLM", "检索", "证据"]  # 验证旧标签响应字段在迁移期保持同内容镜像。
    assert duplicate_response.json()["item"]["paper"]["title"] == "Updated Retrieval Metadata"  # 验证重复收藏刷新论文快照。
    assert duplicate_response.json()["item"]["note"] == "首次收藏"  # 验证未提供新备注时保留旧备注。
    assert list_response.json()["total"] == 1  # 验证数据库中只存在一条论文身份记录。


def test_library_semantic_search_uses_stable_route_and_response_contract(library_client: TestClient) -> None:
    """自然语言文献库检索应在动态 item_id 路由之前匹配并返回稳定响应。"""
    response = library_client.get("/api/v1/library/items/semantic-search", params={"query": "semantic retrieval", "top_k": 5})  # 调用无需真实模型的覆盖检索器。

    assert response.status_code == 200  # 验证固定语义检索路径未被 /{item_id} 路由吞掉。
    assert response.json() == {"items": [], "total": 0, "degraded": False, "degradation_reason": None}  # 验证空语义结果保持稳定公共契约。


def test_library_save_defers_vector_index_until_first_semantic_search(library_client: TestClient) -> None:
    """收藏必须只写 SQLite，用户首次执行语义检索时才触发延迟索引。"""
    _NoopLibraryPaperIndexer.index_calls = 0  # 重置类级同步调用统计，隔离同文件其他用例。
    _NoopLibraryPaperIndexer.async_index_calls = 0  # 重置类级异步调用统计，隔离同文件其他用例。
    saved = library_client.post("/api/v1/library/items", json={"paper": _paper_payload()})  # 保存论文且不允许等待模型。
    before_search = _NoopLibraryPaperIndexer.async_index_calls  # 读取语义检索前的延迟调用次数。
    searched = library_client.get("/api/v1/library/items/semantic-search", params={"query": "semantic retrieval", "top_k": 5})  # 用户首次主动发起自然语言检索。

    assert saved.status_code == 200 and saved.json()["created"] is True  # 验证收藏仍保持原有稳定响应。
    assert _NoopLibraryPaperIndexer.index_calls == 0 and before_search == 0  # 验证收藏操作没有调用同步或异步模型索引。
    assert searched.status_code == 200 and _NoopLibraryPaperIndexer.async_index_calls == 1  # 验证首次语义检索才调用延迟索引入口。


def test_library_filters_updates_and_deletes_item(library_client: TestClient) -> None:
    """文献库应支持关键词与状态筛选、属性更新、删除及不存在边界。"""
    saved = library_client.post("/api/v1/library/items", json={"paper": _paper_payload(), "keywords": ["检索"], "reading_status": "reading"}).json()["item"]  # 保存阅读中的论文。
    item_id = saved["item_id"]  # 提取内部收藏标识。
    matching = library_client.get("/api/v1/library/items", params={"keyword": "检索", "reading_status": "reading"})  # 使用双条件筛选。
    missing = library_client.get("/api/v1/library/items", params={"keyword": "不存在"})  # 查询无匹配关键词。
    updated = library_client.patch(f"/api/v1/library/items/{item_id}", json={"keywords": ["重点"], "note": "", "reading_status": "read"})  # 更新全部用户属性。
    fetched = library_client.get(f"/api/v1/library/items/{item_id}")  # 读取更新后的单条记录。
    deleted = library_client.delete(f"/api/v1/library/items/{item_id}")  # 删除已有收藏。
    deleted_again = library_client.delete(f"/api/v1/library/items/{item_id}")  # 再次删除验证不存在边界。

    assert matching.status_code == 200 and matching.json()["total"] == 1  # 验证关键词和状态联合筛选。
    assert missing.status_code == 200 and missing.json()["total"] == 0  # 验证无匹配时返回空集合而非错误。
    assert updated.status_code == 200 and updated.json()["keywords"] == ["重点"]  # 验证关键词整体替换。
    assert updated.json()["reading_status"] == "read" and updated.json()["note"] == ""  # 验证阅读状态和空备注更新。
    assert fetched.status_code == 200 and fetched.json()["item_id"] == item_id  # 验证单条读取接口。
    assert deleted.status_code == 204 and deleted.content == b""  # 验证删除成功且无响应正文。
    assert deleted_again.status_code == 404 and deleted_again.json()["detail"] == "文献库记录不存在"  # 验证重复删除返回稳定 404。


def test_library_keyword_facets_and_source_keywords_are_selectable(library_client: TestClient) -> None:
    """关键词面板应聚合用户关键词和来源关键词，并支持两者精确筛选。"""
    library_client.post("/api/v1/library/items", json={"paper": _paper_payload(keywords=["Retrieval", "LLM"]), "keywords": ["重点"]})  # 保存同时含来源和用户关键词的论文。
    listed = library_client.get("/api/v1/library/items")  # 读取不带关键词筛选的完整面板。
    source_keyword = library_client.get("/api/v1/library/items", params={"keyword": "retrieval"})  # 使用来源关键词执行大小写无关筛选。
    user_keyword = library_client.get("/api/v1/library/items", params={"keyword": "重点"})  # 使用用户关键词执行筛选。

    facets = listed.json()["keyword_facets"]  # 读取前端关键词选择区依赖的聚合结果。
    assert {facet["keyword"] for facet in facets} == {"Retrieval", "LLM", "重点"}  # 验证两个关键词来源均被展示。
    assert source_keyword.status_code == 200 and source_keyword.json()["total"] == 1  # 验证来源关键词可筛选收藏。
    assert user_keyword.status_code == 200 and user_keyword.json()["total"] == 1  # 验证用户关键词可筛选收藏。


def test_library_filters_by_year_and_venue_sorts_and_pages(library_client: TestClient) -> None:
    """文献库列表应在服务端执行年份、venue、排序和分页，并保持关键词面板处于同一结构化范围。"""
    first = _paper_payload(paper_id="paper-1", doi="10.1000/library-1", title="Zeta Methods")  # 构造较早会议论文。
    first.update({"year": 2021, "venue": "NeurIPS"})  # 设置年份和会议元数据。
    second = _paper_payload(paper_id="paper-2", doi="10.1000/library-2", title="Alpha Methods", keywords=["Vision"])  # 构造较新会议论文。
    second.update({"year": 2024, "venue": "NeurIPS"})  # 设置年份和会议元数据。
    third = _paper_payload(paper_id="paper-3", doi="10.1000/library-3", title="Medical Retrieval")  # 构造不匹配 venue 的论文。
    third.update({"year": 2025, "venue": "Nature Medicine"})  # 设置不同来源期刊。
    for paper in (first, second, third):  # 保存三条独立身份的收藏记录。
        response = library_client.post("/api/v1/library/items", json={"paper": paper})  # 通过真实 API 写入隔离数据库。
        assert response.status_code == 200  # 确保测试前置数据已成功保存。

    paged = library_client.get("/api/v1/library/items", params={"venue": "neurips", "year_start": 2020, "year_end": 2024, "sort": "year_asc", "page": 2, "page_size": 1})  # 请求第二页并按年份正序。
    title_sorted = library_client.get("/api/v1/library/items", params={"venue": "NeurIPS", "sort": "title_asc", "page_size": 10})  # 验证标题排序。
    invalid_range = library_client.get("/api/v1/library/items", params={"year_start": 2025, "year_end": 2020})  # 提交逻辑矛盾范围。

    assert paged.status_code == 200  # 验证组合筛选请求成功。
    assert paged.json()["total"] == 2 and paged.json()["page"] == 2 and paged.json()["total_pages"] == 2  # 验证总数与服务端分页元数据。
    assert [item["paper"]["title"] for item in paged.json()["items"]] == ["Alpha Methods"]  # 验证第二页包含较晚年份论文。
    assert {facet["keyword"] for facet in paged.json()["keyword_facets"]} == {"Vision"}  # 验证关键词面板受年份和 venue 范围约束而非当前页限制。
    assert [item["paper"]["title"] for item in title_sorted.json()["items"]] == ["Alpha Methods", "Zeta Methods"]  # 验证标题排序大小写无关且稳定。
    assert invalid_range.status_code == 422 and invalid_range.json()["detail"] == "起始年份不能晚于结束年份"  # 验证年份范围错误在 API 边界被拒绝。


def test_library_rejects_invalid_status_and_hides_database_error(library_client: TestClient) -> None:
    """无效阅读状态应在业务前被拒绝，数据库异常不得泄露内部细节。"""
    invalid_response = library_client.post("/api/v1/library/items", json={"paper": _paper_payload(), "reading_status": "unknown"})  # 提交非法枚举。

    class FailingLibraryService:
        """模拟保存阶段数据库不可用的服务替身。"""

        def save(self, request: object) -> object:
            """始终抛出 SQLAlchemy 公共异常。"""
            raise SQLAlchemyError("包含内部数据库信息")  # 验证路由隐藏底层错误。

    app.dependency_overrides[get_library_service] = lambda: FailingLibraryService()  # 注入无需真实数据库的失败替身。
    with patch("backend.app.api.routes.library.logger.exception") as log_exception:  # 拦截预期异常日志避免测试噪音。
        failed_response = library_client.post("/api/v1/library/items", json={"paper": _paper_payload()})  # 触发稳定数据库故障边界。

    assert invalid_response.status_code == 422  # 验证无效枚举不会进入仓储。
    assert failed_response.status_code == 503  # 验证数据库故障映射为服务不可用。
    assert failed_response.json()["detail"] == "文献库服务暂时不可用，请稍后重试"  # 验证响应不泄露异常文本。
    log_exception.assert_called_once_with("文献库保存失败")  # 验证完整堆栈仍写入受控日志。
