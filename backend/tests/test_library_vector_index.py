"""验证首次语义检索前的向量写入、同文本复用和失败降级闭环。"""

import asyncio  # 在同步 pytest 用例中执行索引器的异步主入口。
from dataclasses import dataclass, field  # 构造不依赖真实模型和 FAISS 的轻量替身。

from sqlalchemy import create_engine  # 创建隔离内存 SQLite 向量元数据表。
from sqlalchemy.orm import Session, sessionmaker  # 创建显式事务测试会话。

from backend.app.models.paper import PaperRecord  # 构造收藏论文输入。
from backend.app.repositories.database import Base  # 使用统一 ORM 元数据创建内存表。
from backend.app.repositories.faiss_index import FaissIndexError  # 模拟索引原子保存失败。
from backend.app.repositories.vector_metadata import EmbeddingRecordRow, IndexMetadataRow, VectorMetadataRepository  # 验证 SQLite 映射生命周期。
from backend.app.services.embedding import EmbeddingBatch  # 构造无需模型的批量向量结果。
from backend.app.services.library_vector_index import LibraryVectorIndexer  # 导入待测延迟索引编排器。


class _StubEmbeddingService:
    """返回固定归一化向量并记录是否发生重复编码。"""

    def __init__(self) -> None:
        """初始化调用计数。"""
        self.calls = 0  # 记录文献库是否错误重复编码同一文本。

    async def encode_documents(self, texts: list[str]) -> EmbeddingBatch:
        """验证单论文输入并返回固定二维单位向量。"""
        self.calls += 1  # 记录一次真实编码请求。
        assert len(texts) == 1 and texts[0].startswith("Title:")  # 验证索引器使用统一论文嵌入文本。
        return EmbeddingBatch(vectors=((1.0, 0.0),), model_name="BAAI/bge-m3", model_revision="test-rev", dimension=2, normalized=True, latency_ms=1, device="cpu")  # 返回 FAISS 可直接接收的单位向量。


@dataclass
class _StubIndexManager:
    """记录写入调用的内存索引替身。"""

    index_name: str = "library"  # 与 SQLite 元数据索引名称保持一致。
    add_calls: list[tuple[list[list[float]], list[int]]] = field(default_factory=list)  # 保存写入向量与稳定 ID。

    def add(self, vectors: list[list[float]], vector_ids: list[int]) -> int:
        """记录写入参数并返回模拟索引总数。"""
        self.add_calls.append((vectors, vector_ids))  # 验证 pending ID 被传给 FAISS 层。
        return len(self.add_calls)  # 返回对本测试足够的非零索引数量。


class _FailingIndexManager(_StubIndexManager):
    """模拟 FAISS 原子保存失败，验证 pending 映射会被标记 failed。"""

    def add(self, vectors: list[list[float]], vector_ids: list[int]) -> int:
        """记录调用后抛出已净化索引错误。"""
        self.add_calls.append((vectors, vector_ids))  # 记录失败前确实尝试写入。
        raise FaissIndexError("索引保存失败")  # 触发服务安全降级和 failed 状态更新。


def _metadata_repository() -> tuple[VectorMetadataRepository, Session]:
    """创建仅用于向量写入测试的内存 SQLite 仓储。"""
    engine = create_engine("sqlite://")  # 不触碰 data 目录或真实收藏数据。
    _ = EmbeddingRecordRow, IndexMetadataRow  # 明确导入用于注册阶段五 ORM 映射。
    Base.metadata.create_all(bind=engine)  # 创建向量映射和索引元数据表。
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()  # 创建显式提交会话。
    return VectorMetadataRepository(session), session  # 返回待测仓储和供清理使用的会话。


def _paper(abstract: str = "A semantic retrieval paper.") -> PaperRecord:
    """构造可进入文献库语义索引的最小统一论文记录。"""
    return PaperRecord(paper_id="paper-1", title="Semantic Paper", abstract=abstract, source="manual", keywords=["retrieval"], year=2025)  # 提供文本构造器所需的公开字段。


def test_index_writes_pending_vector_then_activates_and_reuses_same_text() -> None:
    """首次语义检索应写入 active 映射；相同文本再次检索不得重复编码或写入。"""
    repository, session = _metadata_repository()  # 创建隔离 SQLite 向量元数据仓储。
    embedding_service = _StubEmbeddingService()  # 使用无需模型的编码器替身。
    index_manager = _StubIndexManager()  # 使用无需 FAISS 的索引替身。
    indexer = LibraryVectorIndexer(embedding_service=embedding_service, index_manager=index_manager)  # 装配待测延迟索引器。
    try:  # 确保测试结束关闭数据库会话。
        first = asyncio.run(indexer.index_async(_paper(), repository))  # 执行异步主入口的完整文本、向量、pending、写入和激活流程。
        second = asyncio.run(indexer.index_async(_paper(), repository))  # 使用相同论文文本再次调用异步主入口。

        assert first.indexed is True and first.vector_id is not None  # 验证首次调用完成可检索向量写入。
        assert second.indexed is True and second.reason == "已复用现有向量"  # 验证第二次调用识别 active 缓存。
        assert embedding_service.calls == 1 and len(index_manager.add_calls) == 1  # 验证相同 text_hash 不重复消耗模型或写 FAISS。
        assert index_manager.add_calls[0][1] == [first.vector_id]  # 验证 FAISS 使用 SQLite 预分配稳定 ID。
        assert repository.active_count("library") == 1  # 验证 SQLite 仅暴露一个可检索向量。
    finally:  # 无论断言是否失败都关闭会话。
        session.close()  # 释放内存数据库连接。


def test_index_marks_pending_record_failed_when_faiss_write_fails() -> None:
    """FAISS 失败不得回滚收藏；对应 pending 映射必须标记 failed。"""
    repository, session = _metadata_repository()  # 创建隔离 SQLite 向量元数据仓储。
    indexer = LibraryVectorIndexer(embedding_service=_StubEmbeddingService(), index_manager=_FailingIndexManager())  # 装配会抛出索引错误的替身。
    try:  # 确保测试结束关闭会话。
        result = indexer.index(_paper(), repository)  # 执行失败路径。
        built_text_hash = indexer._text_builder.build_embedding_text(_paper()).text_hash  # 读取与写入相同的稳定文本哈希验证状态。
        record = repository.find("library", "paper-1", built_text_hash)  # 查询失败后保留的 SQLite 映射。

        assert result.indexed is False and result.reason == "语义索引暂不可用，已保留收藏"  # 验证调用方获得可解释降级而非模型底层错误。
        assert record is not None and record.status == "failed"  # 验证失败向量不会被检索误用。
    finally:  # 无论断言是否失败都关闭会话。
        session.close()  # 释放内存数据库连接。
