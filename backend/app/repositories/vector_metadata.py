"""持久化向量与论文映射、索引状态及可重建索引元数据。"""

from datetime import datetime, timezone  # 使用 UTC 时间记录向量状态和索引更新时间。
from typing import Literal  # 限制向量生命周期状态的稳定取值。

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, select  # 声明 SQLite 表字段和稳定查询。
from sqlalchemy.orm import Mapped, Session, mapped_column  # 定义 ORM 映射和请求级仓储会话。

from backend.app.repositories.database import Base  # 注册到统一 SQLAlchemy 元数据。


EmbeddingRecordStatus = Literal["pending", "active", "inactive", "failed"]  # 区分 FAISS 写入前后与逻辑失效状态。


class EmbeddingRecordRow(Base):
    """映射一条论文文本在指定索引中的向量生命周期记录。"""

    __tablename__ = "embedding_records"  # 使用阶段五规划要求的稳定表名。
    __table_args__ = (UniqueConstraint("index_name", "paper_id", "text_hash", name="uq_embedding_record_identity"),)  # 同一索引、论文和文本版本只保留一条映射。

    vector_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # SQLite Integer 主键提供稳定 64 位可用向量 ID。
    index_name: Mapped[str] = mapped_column(String(64), index=True)  # 区分 global_papers 与 library 等独立 FAISS 索引。
    paper_id: Mapped[str] = mapped_column(String(255), index=True)  # 保存统一 PaperRecord 内部标识。
    text_hash: Mapped[str] = mapped_column(String(64), index=True)  # 记录模型、文本版本和内容相关哈希。
    builder_version: Mapped[str] = mapped_column(String(64))  # 保存 PaperTextBuilder 格式版本。
    model_name: Mapped[str] = mapped_column(String(255))  # 保存生成当前向量的模型标识。
    model_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 保存可选权重修订版本。
    dimension: Mapped[int] = mapped_column(Integer)  # 保存向量维度，供重建和兼容性校验。
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # 保存 pending、active、inactive 或 failed 状态。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 保存首次分配稳定 vector_id 的 UTC 时间。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 保存最后一次状态转换时间。


class IndexMetadataRow(Base):
    """映射每个 FAISS 文件的可重建元数据与当前活动向量数量。"""

    __tablename__ = "index_metadata"  # 使用阶段五规划要求的稳定表名。

    index_name: Mapped[str] = mapped_column(String(64), primary_key=True)  # 使用索引逻辑名称作为稳定主键。
    dimension: Mapped[int] = mapped_column(Integer)  # 保存 IndexFlatIP 的固定维度。
    model_name: Mapped[str] = mapped_column(String(255))  # 保存与该索引兼容的嵌入模型。
    model_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 保存可选权重修订。
    active_vector_count: Mapped[int] = mapped_column(Integer, default=0)  # 保存 SQLite 视角的活跃向量数。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 保存最近一次索引成功更新的 UTC 时间。


class VectorMetadataRepository:
    """封装向量 ID 分配、状态转换、活动映射查询和索引元数据更新。"""

    def __init__(self, session: Session) -> None:
        """保存由调用方注入的请求级或任务级 SQLite 会话。"""
        self._session = session  # 避免仓储依赖全局会话导致事务边界不清晰。

    def reserve_pending(self, index_name: str, paper_id: str, text_hash: str, builder_version: str, model_name: str, model_revision: str | None, dimension: int) -> EmbeddingRecordRow:
        """获取或创建 pending 映射，为 FAISS add_with_ids 预分配稳定 vector_id。"""
        row = self._session.scalar(select(EmbeddingRecordRow).where(EmbeddingRecordRow.index_name == index_name, EmbeddingRecordRow.paper_id == paper_id, EmbeddingRecordRow.text_hash == text_hash))  # 检查相同文本是否已有可复用映射。
        if row is not None:  # 相同内容不重复分配向量 ID。
            return row  # 允许调用方按现有状态决定复用或恢复。
        now = datetime.now(timezone.utc)  # 为本次预写生成统一 UTC 时间。
        row = EmbeddingRecordRow(index_name=index_name, paper_id=paper_id, text_hash=text_hash, builder_version=builder_version, model_name=model_name, model_revision=model_revision, dimension=dimension, status="pending", created_at=now, updated_at=now)  # 在 FAISS 写入前创建可恢复的 pending 记录。
        self._session.add(row)  # 纳入当前显式事务。
        self._session.commit()  # 先持久化 vector_id，防止索引使用不稳定内存 ID。
        self._session.refresh(row)  # 读取 SQLite 自动分配的稳定整数 ID。
        return row  # 返回可直接传给 FAISS 的映射记录。

    def find(self, index_name: str, paper_id: str, text_hash: str) -> EmbeddingRecordRow | None:
        """按索引、论文和文本哈希查询可复用或待恢复的向量映射。"""
        return self._session.scalar(select(EmbeddingRecordRow).where(EmbeddingRecordRow.index_name == index_name, EmbeddingRecordRow.paper_id == paper_id, EmbeddingRecordRow.text_hash == text_hash))  # 返回相同内容的唯一映射或空值。

    def activate(self, vector_id: int) -> EmbeddingRecordRow:
        """在 FAISS 原子保存成功后将预写记录切换为 active。"""
        row = self._get_required(vector_id)  # 确保状态转换目标存在。
        row.status = "active"  # 仅成功写入并保存索引后允许检索命中。
        row.updated_at = datetime.now(timezone.utc)  # 记录状态切换时间。
        self._session.commit()  # 持久化活动状态供查询过滤使用。
        self._session.refresh(row)  # 返回数据库最终值。
        return row  # 供调用方记录或组合响应。

    def activate_replacing(self, vector_id: int) -> EmbeddingRecordRow:
        """激活新向量并在同一事务中逻辑失效该论文在当前索引中的旧 active 向量。"""
        row = self._get_required(vector_id)  # 读取刚完成 FAISS 原子写入的 pending 映射。
        now = datetime.now(timezone.utc)  # 使用同一 UTC 时间标记替换事务。
        previous_rows = self._session.scalars(select(EmbeddingRecordRow).where(EmbeddingRecordRow.index_name == row.index_name, EmbeddingRecordRow.paper_id == row.paper_id, EmbeddingRecordRow.status == "active", EmbeddingRecordRow.vector_id != row.vector_id)).all()  # 查找同一论文文本更新前仍在检索中的旧向量。
        for previous_row in previous_rows:  # 一篇论文可能因历史异常存在多个活跃向量。
            previous_row.status = "inactive"  # 防止旧摘要或旧模型文本继续被检索命中。
            previous_row.updated_at = now  # 记录统一替换时间供后续重建策略使用。
        row.status = "active"  # 新索引文件已成功发布后才允许当前向量进入检索集合。
        row.updated_at = now  # 记录新向量激活时间。
        self._session.commit()  # 原子提交新旧映射切换，避免同时活跃的长期状态。
        self._session.refresh(row)  # 读取数据库最终状态。
        return row  # 返回当前激活映射。

    def mark_inactive(self, vector_id: int) -> EmbeddingRecordRow:
        """逻辑失效旧向量，不在删除时重建整个 FAISS 索引。"""
        row = self._get_required(vector_id)  # 确保目标记录存在。
        row.status = "inactive"  # 搜索阶段将通过 SQLite 映射过滤该向量。
        row.updated_at = datetime.now(timezone.utc)  # 记录失效时间供后续重建策略判断。
        self._session.commit()  # 提交逻辑删除状态。
        self._session.refresh(row)  # 返回最终记录。
        return row  # 供调用方继续更新索引元数据。

    def mark_failed(self, vector_id: int) -> EmbeddingRecordRow:
        """记录 FAISS 写入失败的 pending 向量，便于后续恢复或重新编码。"""
        row = self._get_required(vector_id)  # 确保目标记录存在。
        row.status = "failed"  # 禁止失败映射参与检索或被误认作可复用向量。
        row.updated_at = datetime.now(timezone.utc)  # 记录失败时间。
        self._session.commit()  # 持久化失败状态。
        self._session.refresh(row)  # 返回最终记录。
        return row  # 由上层决定是否安排重试。

    def active_vector_ids(self, index_name: str, vector_ids: list[int]) -> set[int]:
        """返回候选 ID 中仍映射到 active 论文文本的向量集合。"""
        if not vector_ids:  # 避免生成无意义 IN 空集合查询。
            return set()  # 返回稳定空集合。
        rows = self._session.scalars(select(EmbeddingRecordRow.vector_id).where(EmbeddingRecordRow.index_name == index_name, EmbeddingRecordRow.status == "active", EmbeddingRecordRow.vector_id.in_(vector_ids))).all()  # 仅查询本索引内仍有效的映射。
        return {int(vector_id) for vector_id in rows}  # 转换为供 FAISS 搜索结果过滤使用的整数集合。

    def active_count(self, index_name: str) -> int:
        """返回指定索引在 SQLite 映射中仍有效的向量数量。"""
        return len(self._session.scalars(select(EmbeddingRecordRow.vector_id).where(EmbeddingRecordRow.index_name == index_name, EmbeddingRecordRow.status == "active")).all())  # 使用 SQLite 状态而非 FAISS 总数统计逻辑失效后的真实可检索数量。

    def upsert_index_metadata(self, index_name: str, dimension: int, model_name: str, model_revision: str | None, active_vector_count: int) -> IndexMetadataRow:
        """创建或刷新索引元数据，记录可重建模型和活动向量统计。"""
        row = self._session.get(IndexMetadataRow, index_name)  # 按逻辑索引名称读取现有元数据。
        now = datetime.now(timezone.utc)  # 为本次索引成功写入生成统一时间。
        if row is None:  # 首次创建该索引的元数据。
            row = IndexMetadataRow(index_name=index_name, dimension=dimension, model_name=model_name, model_revision=model_revision, active_vector_count=active_vector_count, updated_at=now)  # 保存重建索引所需的全部关键事实。
            self._session.add(row)  # 纳入当前事务。
        else:  # 已有索引仅刷新最新统计和模型标识。
            row.dimension = dimension  # 保持元数据与实际 FAISS 维度一致。
            row.model_name = model_name  # 更新当前模型标识。
            row.model_revision = model_revision  # 更新可选模型修订。
            row.active_vector_count = active_vector_count  # 更新 SQLite 视角的活动映射数。
            row.updated_at = now  # 记录元数据刷新时间。
        self._session.commit()  # 原子保存索引元数据。
        self._session.refresh(row)  # 读取最终数据库值。
        return row  # 返回可供上层记录的稳定对象。

    def _get_required(self, vector_id: int) -> EmbeddingRecordRow:
        """读取指定向量记录，不存在时返回稳定业务错误。"""
        row = self._session.get(EmbeddingRecordRow, vector_id)  # 使用主键高效读取映射。
        if row is None:  # 禁止对不存在向量静默成功。
            raise LookupError("向量映射不存在")  # 由上层转换为任务或 API 领域错误。
        return row  # 返回状态转换的目标记录。
