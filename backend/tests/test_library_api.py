"""验证个人文献库 SQLite 闭环、去重规则和稳定 HTTP 错误边界。"""

from collections.abc import Iterator  # 标注测试夹具的会话生成器。
from unittest.mock import patch  # 隔离预期数据库错误日志。

import pytest  # 提供测试夹具。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证完整 API。
from sqlalchemy import create_engine  # 创建隔离内存 SQLite 引擎。
from sqlalchemy.exc import SQLAlchemyError  # 模拟持久化故障。
from sqlalchemy.orm import Session, sessionmaker  # 创建测试会话工厂。
from sqlalchemy.pool import StaticPool  # 让多线程测试客户端共享同一内存数据库。

from backend.app.api.routes.library import get_database_session, get_library_paper_indexer, get_library_service  # 覆盖生产数据库、模型和服务依赖。
from backend.app.services.library_vector_index import LibraryVectorIndexResult  # 构造不加载真实模型的收藏后索引替身。
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
    client = TestClient(app)  # 构造不启动真实服务的本地客户端。
    yield client  # 交给用例验证完整路由闭环。
    client.close()  # 释放 ASGI 客户端资源。
    app.dependency_overrides.pop(get_database_session, None)  # 清理会话覆盖防止污染其他测试。
    app.dependency_overrides.pop(get_library_paper_indexer, None)  # 清理模型索引器覆盖防止污染其他测试。
    app.dependency_overrides.pop(get_library_service, None)  # 清理可能由异常用例设置的服务覆盖。
    Base.metadata.drop_all(bind=engine)  # 删除内存表以隔离下一用例。
    engine.dispose()  # 释放内存数据库连接。


class _NoopLibraryPaperIndexer:
    """在 API 单测中替代真实模型和 FAISS 的无操作收藏后索引器。"""

    def index(self, paper: PaperRecord, metadata_repository: object) -> LibraryVectorIndexResult:
        """不加载模型、不写入索引，仅返回可解释测试降级结果。"""
        _ = paper, metadata_repository  # 明确测试替身不会读取论文内容或 SQLite 映射。
        return LibraryVectorIndexResult(indexed=False, vector_id=None, reason="测试未启用语义索引")  # 保持收藏 API 响应契约不变。


def _paper_payload(paper_id: str = "paper-1", doi: str = "10.1000/library-test", title: str = "Evidence-Grounded Retrieval") -> dict[str, object]:
    """构造满足 PaperRecord 契约的最小论文 JSON。"""
    return {"paper_id": paper_id, "title": title, "abstract": "A retrieval study.", "authors": [{"name": "Ada Lovelace"}], "year": 2025, "doi": doi, "source": "manual"}  # 返回可直接提交的 JSON 对象。


def test_library_identity_uses_doi_then_source_identifiers() -> None:
    """收藏去重键应优先使用 DOI，并在缺失时依次回退到来源标识。"""
    doi_paper = PaperRecord(paper_id="internal-1", title="DOI Paper", source="manual", doi="https://doi.org/10.1000/EXAMPLE", arxiv_id="2501.00001")  # 同时提供 DOI 和 arXiv ID。
    arxiv_paper = PaperRecord(paper_id="internal-2", title="arXiv Paper", source="manual", arxiv_id="2501.00002")  # 仅提供 arXiv 来源标识。
    internal_paper = PaperRecord(paper_id="INTERNAL-3", title="Internal Paper", source="manual")  # 仅保留必填内部 ID。

    assert build_library_identity_key(doi_paper) == "doi:10.1000/example"  # 验证 DOI URL 规范化且优先于 arXiv ID。
    assert build_library_identity_key(arxiv_paper) == "arxiv:2501.00002"  # 验证 DOI 缺失时回退 arXiv ID。
    assert build_library_identity_key(internal_paper) == "paper:internal-3"  # 验证全部来源标识缺失时回退内部 ID。


def test_library_starts_empty_and_deduplicates_doi_saves(library_client: TestClient) -> None:
    """空文献库应可查询，重复 DOI 收藏应刷新快照并合并标签。"""
    empty_response = library_client.get("/api/v1/library/items")  # 查询尚未保存论文的文献库。
    first_response = library_client.post("/api/v1/library/items", json={"paper": _paper_payload(), "tags": ["LLM", "检索"], "note": "首次收藏"})  # 创建首条收藏。
    duplicate_response = library_client.post("/api/v1/library/items", json={"paper": _paper_payload(paper_id="other-source-id", doi="https://doi.org/10.1000/LIBRARY-TEST", title="Updated Retrieval Metadata"), "tags": ["llm", "证据"]})  # 使用同 DOI 不同表现形式重复收藏。
    list_response = library_client.get("/api/v1/library/items")  # 查询去重后的完整集合。

    assert empty_response.status_code == 200 and empty_response.json() == {"items": [], "total": 0}  # 验证空结果边界稳定。
    assert first_response.status_code == 200 and first_response.json()["created"] is True  # 验证首次保存实际创建记录。
    assert duplicate_response.status_code == 200 and duplicate_response.json()["created"] is False  # 验证规范化 DOI 命中已有记录。
    assert duplicate_response.json()["item"]["tags"] == ["LLM", "检索", "证据"]  # 验证标签大小写无关合并并保留首次形式。
    assert duplicate_response.json()["item"]["paper"]["title"] == "Updated Retrieval Metadata"  # 验证重复收藏刷新论文快照。
    assert duplicate_response.json()["item"]["note"] == "首次收藏"  # 验证未提供新备注时保留旧备注。
    assert list_response.json()["total"] == 1  # 验证数据库中只存在一条论文身份记录。


def test_library_filters_updates_and_deletes_item(library_client: TestClient) -> None:
    """文献库应支持标签与状态筛选、属性更新、删除及不存在边界。"""
    saved = library_client.post("/api/v1/library/items", json={"paper": _paper_payload(), "tags": ["检索"], "reading_status": "reading"}).json()["item"]  # 保存阅读中的论文。
    item_id = saved["item_id"]  # 提取内部收藏标识。
    matching = library_client.get("/api/v1/library/items", params={"tag": "检索", "reading_status": "reading"})  # 使用双条件筛选。
    missing = library_client.get("/api/v1/library/items", params={"tag": "不存在"})  # 查询无匹配标签。
    updated = library_client.patch(f"/api/v1/library/items/{item_id}", json={"tags": ["重点"], "note": "", "reading_status": "read"})  # 更新全部用户属性。
    fetched = library_client.get(f"/api/v1/library/items/{item_id}")  # 读取更新后的单条记录。
    deleted = library_client.delete(f"/api/v1/library/items/{item_id}")  # 删除已有收藏。
    deleted_again = library_client.delete(f"/api/v1/library/items/{item_id}")  # 再次删除验证不存在边界。

    assert matching.status_code == 200 and matching.json()["total"] == 1  # 验证标签和状态联合筛选。
    assert missing.status_code == 200 and missing.json()["total"] == 0  # 验证无匹配时返回空集合而非错误。
    assert updated.status_code == 200 and updated.json()["tags"] == ["重点"]  # 验证标签整体替换。
    assert updated.json()["reading_status"] == "read" and updated.json()["note"] == ""  # 验证阅读状态和空备注更新。
    assert fetched.status_code == 200 and fetched.json()["item_id"] == item_id  # 验证单条读取接口。
    assert deleted.status_code == 204 and deleted.content == b""  # 验证删除成功且无响应正文。
    assert deleted_again.status_code == 404 and deleted_again.json()["detail"] == "文献库记录不存在"  # 验证重复删除返回稳定 404。


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
