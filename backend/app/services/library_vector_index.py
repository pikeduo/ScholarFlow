"""编排文献库收藏后的文本构造、BGE 向量生成与 FAISS 原子索引写入。"""

import asyncio  # 在同步文献库服务中受控调用异步嵌入接口。
from dataclasses import dataclass  # 使用稳定结果对象报告索引成功或可解释降级。
from pathlib import Path  # 保存默认文献库 FAISS 文件路径。
from typing import Protocol  # 声明可替换索引器协议，避免业务层绑定具体实现。

from backend.app.core.config import PROJECT_ROOT  # 将默认索引文件稳定锚定到仓库根目录。
from backend.app.core.logging import logger  # 记录不含论文正文的索引成功、复用和降级统计。
from backend.app.models.paper import PaperRecord  # 接收已规范化的收藏论文。
from backend.app.repositories.faiss_index import FaissIndexError, FaissIndexManager  # 依赖可重建的 FAISS 索引访问层。
from backend.app.repositories.vector_metadata import VectorMetadataRepository  # 依赖 SQLite 向量状态和索引元数据仓储。
from backend.app.services.embedding import EmbeddingService, EmbeddingServiceError  # 调用可替换的 BGE-M3 批量嵌入服务。
from backend.app.services.paper_text import PaperTextBuilder  # 复用与语义粗排一致的版本化论文文本。


LIBRARY_INDEX_NAME = "library"  # 定义 SQLite 元数据与 FAISS 文件共享的文献库索引名称。
DEFAULT_LIBRARY_INDEX_PATH = PROJECT_ROOT / "data" / "faiss" / "library.index"  # 定义受 Git 忽略且不随工作目录变化的默认索引位置。


@dataclass(frozen=True)
class LibraryVectorIndexResult:
    """说明收藏论文本次是否成功进入文献库语义索引。"""

    indexed: bool  # 标记是否完成 FAISS 保存和 SQLite active 状态切换。
    vector_id: int | None  # 成功或可复用时返回稳定向量 ID，降级时为空。
    reason: str | None  # 返回可展示但不泄露模型路径、论文文本或底层异常的摘要。


class LibraryPaperIndexer(Protocol):
    """约束文献库服务需要的最小收藏后向量写入能力。"""

    def index(self, paper: PaperRecord, metadata_repository: VectorMetadataRepository) -> LibraryVectorIndexResult:
        """为收藏论文写入或复用文献库向量，并返回稳定结果。"""
        ...  # Protocol 允许 API 测试注入不加载模型的替身。


class LibraryVectorIndexer:
    """按“文本→BGE→pending→FAISS→active”顺序执行文献库向量写入。

    参数：
        embedding_service：可替换的批量嵌入服务。
        text_builder：与检索排序共用的论文文本构造器。
        index_manager：管理 library FAISS 文件的线程安全仓储。
    """

    def __init__(self, embedding_service: EmbeddingService | None = None, text_builder: PaperTextBuilder | None = None, index_manager: FaissIndexManager | None = None) -> None:
        """保存可替换组件，不在构造时下载模型、加载索引或创建数据文件。"""
        self._embedding_service = embedding_service or EmbeddingService()  # 默认使用阶段五实现的懒加载 BGE 服务。
        self._text_builder = text_builder or PaperTextBuilder()  # 默认使用稳定文本和哈希规则。
        self._index_manager = index_manager or FaissIndexManager(LIBRARY_INDEX_NAME, DEFAULT_LIBRARY_INDEX_PATH)  # 默认管理受 Git 忽略的文献库索引文件。

    def index(self, paper: PaperRecord, metadata_repository: VectorMetadataRepository) -> LibraryVectorIndexResult:
        """写入或复用论文向量；模型或索引不可用时保留收藏成功并返回降级结果。"""
        try:  # 在模型、SQLite 和 FAISS 边界统一执行可解释降级。
            built_text = self._text_builder.build_embedding_text(paper)  # 构造与 BGE 粗排一致的论文语义文本和文本哈希。
            existing = metadata_repository.find(self._index_manager.index_name, paper.paper_id, built_text.text_hash)  # 在编码前检查相同模型和文本是否已有映射。
            if existing is not None and existing.status == "active":  # 已有 active 向量无需重复消耗模型或写入索引。
                logger.info("文献库向量复用：索引=%s，状态=active", self._index_manager.index_name)  # 仅记录安全索引状态。
                return LibraryVectorIndexResult(indexed=True, vector_id=existing.vector_id, reason="已复用现有向量")  # 返回可复用的稳定向量 ID。
            if existing is not None and existing.status == "pending":  # 进程异常中断后的 pending 记录不能直接重复写入同一 ID。
                logger.warning("文献库向量待恢复：索引=%s，状态=pending", self._index_manager.index_name)  # 提示后续重建任务处理半完成状态。
                return LibraryVectorIndexResult(indexed=False, vector_id=existing.vector_id, reason="向量写入待恢复")  # 收藏保留，避免重复 ID 写入。
            embedding_batch = asyncio.run(self._embedding_service.encode_documents([built_text.text]))  # 同步文献库服务在工作线程中受控等待单论文异步编码。
            vector = list(embedding_batch.vectors[0])  # 取出与唯一文本对应的归一化向量并转换为 FAISS 输入。
            record = metadata_repository.reserve_pending(self._index_manager.index_name, paper.paper_id, built_text.text_hash, built_text.builder_version, embedding_batch.model_name, embedding_batch.model_revision, embedding_batch.dimension)  # 在 FAISS 写入前持久化稳定 vector_id。
            self._index_manager.add([vector], [record.vector_id])  # 写入临时索引并在校验后原子发布正式文件。
            active_record = metadata_repository.activate_replacing(record.vector_id)  # 成功发布后激活新向量并逻辑失效同论文旧文本。
            metadata_repository.upsert_index_metadata(self._index_manager.index_name, embedding_batch.dimension, embedding_batch.model_name, embedding_batch.model_revision, metadata_repository.active_count(self._index_manager.index_name))  # 保存可重建模型、维度与活跃数量。
        except (EmbeddingServiceError, FaissIndexError, LookupError, ValueError, RuntimeError):  # 捕获已净化模型、索引、异步运行和元数据错误。
            if "record" in locals():  # 仅在已预写 pending 映射后尝试标记失败。
                try:  # 避免失败状态写入再次掩盖原始索引错误。
                    metadata_repository.mark_failed(record.vector_id)  # 让后续重建或诊断识别未完成向量。
                except Exception:  # 数据库已不可用时只记录受控堆栈。
                    logger.exception("文献库失败向量状态更新失败")  # 不输出论文正文或数据库路径。
            logger.exception("文献库向量写入降级：索引=%s", self._index_manager.index_name)  # 保留完整堆栈但不记录用户论文内容。
            return LibraryVectorIndexResult(indexed=False, vector_id=None, reason="语义索引暂不可用，已保留收藏")  # 收藏 API 仍保持可用并返回安全摘要给服务层。
        logger.info("文献库向量写入完成：索引=%s，维度=%d，设备=%s", self._index_manager.index_name, embedding_batch.dimension, embedding_batch.device)  # 记录索引阶段统计而不记录论文标识或文本。
        return LibraryVectorIndexResult(indexed=True, vector_id=active_record.vector_id, reason=None)  # 返回成功写入的稳定映射 ID。
