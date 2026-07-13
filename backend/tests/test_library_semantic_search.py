"""验证文献库自然语言语义检索的 SQLite 映射过滤与本地降级。"""

import asyncio  # 使用标准库运行异步检索接口，无需新增 pytest 异步插件。
from datetime import datetime, timezone  # 构造符合文献库响应契约的 UTC 时间。
from sqlalchemy import create_engine  # 创建隔离内存 SQLite 向量元数据表。
from sqlalchemy.orm import Session, sessionmaker  # 创建显式事务测试会话。

from backend.app.models.library import LibraryItem  # 构造已完成结构化筛选的收藏列表。
from backend.app.models.paper import PaperRecord  # 构造语义检索论文快照。
from backend.app.repositories.database import Base  # 使用统一 ORM 元数据创建内存表。
from backend.app.repositories.faiss_index import IndexSearchHit  # 构造不依赖真实 FAISS 的候选命中。
from backend.app.repositories.vector_metadata import EmbeddingRecordRow, IndexMetadataRow, VectorMetadataRepository  # 构造并验证 active 向量映射。
from backend.app.services.embedding import EmbeddingBatch, EmbeddingServiceError  # 构造查询向量成功和失败替身。
from backend.app.services.library_semantic_search import LibrarySemanticSearchService  # 导入待测自然语言检索服务。


class _StubEmbeddingService:
    """返回固定查询单位向量，不加载 BGE-M3 模型。"""

    async def encode_queries(self, texts: list[str]) -> EmbeddingBatch:
        """验证自然语言查询输入并返回二维单位向量。"""
        assert texts == ["semantic retrieval"]  # 验证服务不篡改用户查询后才传给本地模型。
        return EmbeddingBatch(vectors=((1.0, 0.0),), model_name="BAAI/bge-m3", model_revision=None, dimension=2, normalized=True, latency_ms=1, device="cpu")  # 返回可供 FAISS 查询的固定向量。


class _FailingEmbeddingService:
    """模拟 BGE-M3 不可用，触发本地词项匹配降级。"""

    async def encode_queries(self, texts: list[str]) -> EmbeddingBatch:
        """始终抛出已净化模型错误。"""
        _ = texts  # 不记录测试自然语言查询内容。
        raise EmbeddingServiceError("模型不可用")  # 触发服务安全降级路径。


class _StubIndexManager:
    """返回预设 FAISS 命中并记录 Top-Kx 查询参数。"""

    def __init__(self, hits: list[IndexSearchHit]) -> None:
        """保存预设排序候选。"""
        self.index_name = "library"  # 与 SQLite 向量元数据索引名称保持一致。
        self._hits = hits  # 保存不依赖 FAISS 的稳定命中集合。
        self.calls: list[tuple[int, int]] = []  # 记录 top_k 与候选倍数参数。

    def search(self, query_vector: list[float], top_k: int, active_vector_ids: set[int] | None = None, candidate_multiplier: int = 3) -> list[IndexSearchHit]:
        """验证服务先请求 Top-Kx 原始候选，再返回预设命中。"""
        assert query_vector == [1.0, 0.0] and active_vector_ids is None  # 验证 SQLite 过滤由服务在 FAISS 候选之后执行。
        self.calls.append((top_k, candidate_multiplier))  # 记录候选扩展策略。
        return self._hits  # 返回已按分数降序排列的候选。


def _metadata_repository() -> tuple[VectorMetadataRepository, Session]:
    """创建隔离 SQLite 向量元数据仓储。"""
    engine = create_engine("sqlite://")  # 不触碰真实数据库或 data 目录。
    _ = EmbeddingRecordRow, IndexMetadataRow  # 明确导入用于注册阶段五 ORM 映射。
    Base.metadata.create_all(bind=engine)  # 创建向量映射表。
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()  # 创建显式提交会话。
    return VectorMetadataRepository(session), session  # 返回待测仓储和供清理使用的会话。


def _item(item_id: str, paper_id: str, title: str) -> LibraryItem:
    """构造当前结构化筛选允许返回的收藏记录。"""
    now = datetime.now(timezone.utc)  # 使用合法 UTC 时间满足响应模型契约。
    paper = PaperRecord(paper_id=paper_id, title=title, abstract="Semantic retrieval benchmark", source="manual", keywords=["retrieval"])  # 提供降级词项和向量映射所需公开元数据。
    return LibraryItem(item_id=item_id, paper=paper, keywords=["检索"], note=None, reading_status="unread", saved_at=now, updated_at=now)  # 返回完整收藏对象。


def test_search_filters_faiss_hits_by_active_mapping_and_current_items() -> None:
    """FAISS 候选应经过 active 状态和结构化筛选集合过滤后再返回。"""
    repository, session = _metadata_repository()  # 创建隔离向量元数据仓储。
    try:  # 确保测试结束关闭会话。
        excluded_record = repository.reserve_pending("library", "paper-excluded", "a" * 64, "paper_embedding_text_v1", "BAAI/bge-m3", None, 2)  # 创建高分但不在当前标签筛选集合的映射。
        allowed_record = repository.reserve_pending("library", "paper-allowed", "b" * 64, "paper_embedding_text_v1", "BAAI/bge-m3", None, 2)  # 创建应返回的映射。
        repository.activate(excluded_record.vector_id)  # 标记高分候选为 active。
        repository.activate(allowed_record.vector_id)  # 标记低分候选为 active。
        manager = _StubIndexManager([IndexSearchHit(vector_id=excluded_record.vector_id, score=0.9), IndexSearchHit(vector_id=allowed_record.vector_id, score=0.6)])  # 构造 FAISS 分数顺序。
        service = LibrarySemanticSearchService(embedding_service=_StubEmbeddingService(), index_manager=manager, candidate_multiplier=4)  # 装配待测服务。

        result = asyncio.run(service.search("semantic retrieval", [_item("item-1", "paper-allowed", "Allowed Paper")], repository, top_k=2))  # 仅传入结构化筛选后的允许收藏。

        assert [result_item.item.item_id for result_item in result.items] == ["item-1"]  # 验证不在当前筛选集合的高分候选被排除。
        assert result.items[0].semantic_score == 0.6 and result.degraded is False  # 验证保留 FAISS 分数且未发生降级。
        assert manager.calls == [(8, 1)]  # 验证按 top_k 乘候选倍数请求 Top-Kx。
    finally:  # 无论断言是否失败都关闭会话。
        session.close()  # 释放内存数据库连接。


def test_search_degrades_to_metadata_term_matching_when_embedding_unavailable() -> None:
    """BGE-M3 不可用时应以论文元数据词项匹配返回结果并标记降级。"""
    repository, session = _metadata_repository()  # 创建降级路径仍可使用的隔离仓储。
    try:  # 确保测试结束关闭会话。
        service = LibrarySemanticSearchService(embedding_service=_FailingEmbeddingService(), index_manager=_StubIndexManager([]))  # 使用失败模型和不会被调用的 FAISS 替身。

        result = asyncio.run(service.search("semantic retrieval", [_item("item-1", "paper-1", "Semantic Retrieval")], repository, top_k=1))  # 调用自然语言检索触发降级。

        assert result.degraded is True and result.degradation_reason == "语义索引暂不可用，已按论文元数据词项匹配"  # 验证返回稳定安全降级说明。
        assert [result_item.item.item_id for result_item in result.items] == ["item-1"]  # 验证词项命中论文仍可返回。
    finally:  # 无论断言是否失败都关闭会话。
        session.close()  # 释放内存数据库连接。
