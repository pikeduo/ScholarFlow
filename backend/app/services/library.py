"""编排个人文献库操作并记录不含用户备注的安全统计。"""

from backend.app.core.logging import logger  # 记录收藏数量和操作类型等非敏感统计。
from backend.app.models.library import LibraryItem, LibraryItemList, LibrarySaveResult, LibrarySemanticSearchResult, ReadingStatus, SaveLibraryItemRequest, UpdateLibraryItemRequest  # 接收稳定请求并返回公共领域模型。
from backend.app.repositories.library import LibraryRepository  # 依赖可替换 SQLite 仓储。
from backend.app.repositories.vector_metadata import VectorMetadataRepository  # 使用请求级 SQLite 会话维护向量映射状态。
from backend.app.services.library_vector_index import LibraryPaperIndexer  # 依赖可替换的收藏后语义索引编排器。
from backend.app.services.library_semantic_search import LibrarySemanticSearcher  # 依赖可替换的文献库自然语言检索器。


class LibraryItemNotFoundError(LookupError):
    """表示指定的个人文献库记录不存在。"""


class LibraryService:
    """提供去重收藏、筛选、属性更新和删除的业务边界。"""

    def __init__(self, repository: LibraryRepository, vector_metadata_repository: VectorMetadataRepository | None = None, paper_indexer: LibraryPaperIndexer | None = None, semantic_searcher: LibrarySemanticSearcher | None = None) -> None:
        """保存由 API 或测试注入的文献库仓储。"""
        self._repository = repository  # 服务不依赖全局数据库会话。
        self._vector_metadata_repository = vector_metadata_repository  # 保存可选向量元数据仓储以保持既有直接服务测试兼容。
        self._paper_indexer = paper_indexer  # 保存可选索引器，允许测试或降级场景不加载模型。
        self._semantic_searcher = semantic_searcher  # 保存可选语义检索器，保持既有直接服务测试兼容。

    def save(self, request: SaveLibraryItemRequest) -> LibrarySaveResult:
        """保存论文，并明确返回本次是否创建新收藏。"""
        item, created = self._repository.save(request.paper, request.tags, request.note, request.reading_status)  # 执行身份去重与原子写入。
        if self._vector_metadata_repository is not None and self._paper_indexer is not None:  # 仅在 API 组合根提供完整阶段五依赖时索引收藏论文。
            index_result = self._paper_indexer.index(item.paper, self._vector_metadata_repository)  # 在收藏成功后执行可解释的语义索引写入或降级。
            logger.info("文献库语义索引结果：成功=%s，已复用或写入向量=%s", index_result.indexed, index_result.vector_id is not None)  # 不记录论文标题、ID 或错误底层信息。
        logger.info("文献库保存完成：新建=%s，标签数=%d", created, len(item.tags))  # 不记录标题、备注或完整论文内容。
        return LibrarySaveResult(item=item, created=created)  # 返回稳定保存结果。

    def list(self, tag: str | None = None, reading_status: ReadingStatus | None = None) -> LibraryItemList:
        """按可选标签和阅读状态返回收藏列表。"""
        items = self._repository.list(tag=tag, reading_status=reading_status)  # 执行确定性筛选。
        logger.info("文献库查询完成：结果数=%d，按标签筛选=%s，按状态筛选=%s", len(items), bool(tag), reading_status is not None)  # 只记录筛选是否启用。
        return LibraryItemList(items=items, total=len(items))  # 返回当前筛选集合和总数。

    async def search_semantic(self, query: str, top_k: int, tag: str | None = None, reading_status: ReadingStatus | None = None) -> LibrarySemanticSearchResult:
        """在结构化筛选后的收藏集合中执行自然语言语义检索或安全降级。

        参数：
            query：用户输入的自然语言检索文本，仅传给本地嵌入模型且不写入日志。
            top_k：期望返回的最多结果数。
            tag：可选精确标签筛选。
            reading_status：可选阅读状态筛选。
        返回：
            LibrarySemanticSearchResult：按语义分数排序的收藏，可能标记降级。
        异常：
            RuntimeError：未装配语义检索依赖时抛出稳定错误。
        """
        if self._vector_metadata_repository is None or self._semantic_searcher is None:  # 直接服务测试或未完成组合根时没有检索依赖。
            raise RuntimeError("文献库语义检索尚未装配")  # 由 API 层映射为安全服务不可用错误。
        items = self._repository.list(tag=tag, reading_status=reading_status)  # 先执行文献库结构化筛选，避免无关论文进入语义结果。
        result = await self._semantic_searcher.search(query, items, self._vector_metadata_repository, top_k)  # 在筛选集合内执行 BGE、FAISS 和 SQLite 映射过滤。
        logger.info("文献库自然语言检索完成：候选数=%d，返回数=%d，降级=%s", len(items), result.total, result.degraded)  # 仅记录数量和降级状态，不记录查询或论文正文。
        return result  # 返回稳定 API 响应模型。

    def get(self, item_id: str) -> LibraryItem:
        """读取单条收藏，不存在时抛出稳定业务异常。"""
        item = self._repository.get(item_id)  # 通过内部 ID 查询。
        if item is None:  # 禁止将不存在记录伪装为空对象。
            raise LibraryItemNotFoundError("文献库记录不存在")  # 交由 API 映射为 404。
        return item  # 返回完整收藏记录。

    def update(self, item_id: str, request: UpdateLibraryItemRequest) -> LibraryItem:
        """更新请求明确提交的用户属性。"""
        changes = request.model_dump(exclude_unset=True)  # 区分未提交字段与显式 null 清空备注。
        item = self._repository.update(item_id, changes)  # 执行原子更新。
        if item is None:  # 不允许 PATCH 隐式创建收藏。
            raise LibraryItemNotFoundError("文献库记录不存在")  # 交由 API 映射为 404。
        logger.info("文献库记录更新完成：更新字段数=%d", len(changes))  # 不记录标签、备注或论文正文。
        return item  # 返回更新后的完整记录。

    def delete(self, item_id: str) -> None:
        """删除指定收藏，不存在时抛出稳定业务异常。"""
        if not self._repository.delete(item_id):  # 执行删除并检查目标是否存在。
            raise LibraryItemNotFoundError("文献库记录不存在")  # 交由 API 映射为 404。
        logger.info("文献库记录删除完成")  # 不记录用户或论文标识。
