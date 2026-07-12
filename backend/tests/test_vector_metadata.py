"""验证 SQLite 向量映射的稳定 ID、生命周期转换和索引元数据。"""

from sqlalchemy import create_engine  # 创建不触碰业务数据库的内存 SQLite 引擎。
from sqlalchemy.orm import Session, sessionmaker  # 构造测试专属会话。

from backend.app.repositories.database import Base  # 使用统一 ORM 元数据创建阶段五表。
from backend.app.repositories.vector_metadata import EmbeddingRecordRow, IndexMetadataRow, VectorMetadataRepository  # 导入待测向量持久化边界。


def _repository() -> tuple[VectorMetadataRepository, Session]:
    """创建注册全部 ORM 表的隔离内存仓储和会话。"""
    engine = create_engine("sqlite://")  # 使用当前进程内存库避免写入 data 目录。
    _ = EmbeddingRecordRow, IndexMetadataRow  # 明确导入用于注册 SQLAlchemy 元数据。
    Base.metadata.create_all(bind=engine)  # 创建 vector metadata 与既有业务表。
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()  # 创建显式提交的测试会话。
    return VectorMetadataRepository(session), session  # 返回待测仓储和供清理使用的会话。


def test_reserve_activate_and_inactivate_vector_mapping() -> None:
    """SQLite 应分配稳定 ID，并仅将 active 映射暴露给 FAISS 命中过滤。"""
    repository, session = _repository()  # 创建隔离向量元数据仓储。
    try:  # 确保测试结束关闭会话。
        first = repository.reserve_pending("library", "paper-1", "a" * 64, "paper_embedding_text_v1", "BAAI/bge-m3", None, 1024)  # 预写第一条 pending 映射。
        duplicate = repository.reserve_pending("library", "paper-1", "a" * 64, "paper_embedding_text_v1", "BAAI/bge-m3", None, 1024)  # 使用相同文本哈希重复预写。
        second = repository.reserve_pending("library", "paper-2", "b" * 64, "paper_embedding_text_v1", "BAAI/bge-m3", "rev-1", 1024)  # 预写不同论文映射。
        repository.activate(first.vector_id)  # 模拟第一条向量已原子写入 FAISS。
        repository.activate(second.vector_id)  # 模拟第二条向量已原子写入 FAISS。
        repository.mark_inactive(first.vector_id)  # 模拟论文更新后逻辑失效旧向量。

        assert duplicate.vector_id == first.vector_id  # 验证相同索引、论文和文本不重复分配 ID。
        assert first.vector_id > 0 and second.vector_id > first.vector_id  # 验证 SQLite 分配稳定递增正整数 ID。
        assert repository.active_vector_ids("library", [first.vector_id, second.vector_id]) == {second.vector_id}  # 验证 inactive 记录不会参与检索结果映射。
    finally:  # 无论断言是否失败都关闭内存会话。
        session.close()  # 释放测试数据库连接。


def test_upsert_index_metadata_refreshes_rebuild_information() -> None:
    """索引元数据应保存模型、维度和活动数量，并可原地刷新。"""
    repository, session = _repository()  # 创建隔离向量元数据仓储。
    try:  # 确保测试结束关闭会话。
        created = repository.upsert_index_metadata("library", 1024, "BAAI/bge-m3", None, 2)  # 首次保存索引重建信息。
        updated = repository.upsert_index_metadata("library", 1024, "BAAI/bge-m3", "rev-2", 3)  # 刷新模型修订和活动数量。

        assert created.index_name == "library" and created.dimension == 1024  # 验证首次元数据包含索引身份和维度。
        assert updated.model_revision == "rev-2" and updated.active_vector_count == 3  # 验证重复写入更新而非新增元数据行。
    finally:  # 无论断言是否失败都关闭内存会话。
        session.close()  # 释放测试数据库连接。
