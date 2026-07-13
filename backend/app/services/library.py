"""编排个人文献库操作并记录不含用户备注的安全统计。"""

from __future__ import annotations  # 延迟解析类型，避免 list 方法名遮蔽内置泛型。

from backend.app.core.logging import logger  # 记录收藏数量和操作类型等非敏感统计。
from backend.app.models.library import LibraryItem, LibraryItemList, LibraryKeywordFacet, LibrarySaveResult, LibrarySemanticSearchResult, LibrarySort, ReadingStatus, SaveLibraryItemRequest, UpdateLibraryItemRequest  # 接收稳定请求并返回公共领域模型。
from backend.app.repositories.library import LibraryRepository  # 依赖可替换 SQLite 仓储。
from backend.app.repositories.vector_metadata import VectorMetadataRepository  # 使用请求级 SQLite 会话维护向量映射状态。
from backend.app.services.library_vector_index import LibraryPaperIndexer  # 依赖可替换的首次语义检索前索引编排器。
from backend.app.services.library_semantic_search import LibrarySemanticSearcher  # 依赖可替换的文献库自然语言检索器。


class LibraryItemNotFoundError(LookupError):
    """表示指定的个人文献库记录不存在。"""


class LibraryService:
    """提供去重收藏、筛选、属性更新和删除的业务边界。"""

    def __init__(self, repository: LibraryRepository, vector_metadata_repository: VectorMetadataRepository | None = None, paper_indexer: LibraryPaperIndexer | None = None, semantic_searcher: LibrarySemanticSearcher | None = None) -> None:
        """保存由 API 或测试注入的文献库仓储。"""
        self._repository = repository  # 服务不依赖全局数据库会话。
        self._vector_metadata_repository = vector_metadata_repository  # 保存可选向量元数据仓储以保持既有直接服务测试兼容。
        self._paper_indexer = paper_indexer  # 保存可选索引器，首次语义检索时才允许加载模型。
        self._semantic_searcher = semantic_searcher  # 保存可选语义检索器，保持既有直接服务测试兼容。

    def save(self, request: SaveLibraryItemRequest) -> LibrarySaveResult:
        """保存论文，并明确返回本次是否创建新收藏。"""
        item, created = self._repository.save(request.paper, request.keywords, request.note, request.reading_status)  # 执行身份去重与原子写入。
        logger.info("文献库保存完成：新建=%s，关键词数=%d，语义索引=待首次检索", created, len(item.keywords))  # 收藏只写 SQLite，不加载 BGE-M3、不记录标题、备注或完整论文内容。
        return LibrarySaveResult(item=item, created=created)  # 返回稳定保存结果。

    def list(self, keyword: str | None = None, reading_status: ReadingStatus | None = None, year_start: int | None = None, year_end: int | None = None, venue: str | None = None, sort: LibrarySort = "updated_desc", page: int = 1, page_size: int = 10) -> LibraryItemList:
        """按可选关键词和阅读状态返回收藏列表及可选关键词集合。"""
        facet_items = self._repository.list(reading_status=reading_status, year_start=year_start, year_end=year_end, venue=venue, sort=sort)  # 先读取同一结构化范围，保证选中关键词后筛选区仍完整可见。
        items = self._repository.list(keyword=keyword, reading_status=reading_status, year_start=year_start, year_end=year_end, venue=venue, sort=sort)  # 再按当前关键词执行精确筛选。
        facets = self._build_keyword_facets(facet_items)  # 聚合用户关键词与来源关键词，供前端以按钮方式选择。
        total = len(items)  # 在分页前保留完整筛选集合的总数供前端显示。
        total_pages = max(1, (total + page_size - 1) // page_size)  # 空集合也保留第一页，避免前端出现无效页码。
        effective_page = min(page, total_pages)  # 删除或收窄筛选后自动回退到最后一个可用页面。
        start = (effective_page - 1) * page_size  # 计算当前服务端页面的零基偏移量。
        page_items = items[start:start + page_size]  # 仅返回当前页所需的论文卡片数据。
        logger.info("文献库查询完成：结果数=%d，页码=%d/%d，按关键词筛选=%s，按状态筛选=%s", total, effective_page, total_pages, bool(keyword), reading_status is not None)  # 只记录筛选是否启用和非敏感分页统计。
        return LibraryItemList(items=page_items, total=total, page=effective_page, page_size=page_size, total_pages=total_pages, keyword_facets=facets)  # 返回分页结果、完整数量和关键词面板。

    async def search_semantic(self, query: str, top_k: int, keyword: str | None = None, reading_status: ReadingStatus | None = None, year_start: int | None = None, year_end: int | None = None, venue: str | None = None) -> LibrarySemanticSearchResult:
        """在结构化筛选后的收藏集合中执行自然语言语义检索或安全降级。

        参数：
            query：用户输入的自然语言检索文本，仅传给本地嵌入模型且不写入日志。
            top_k：期望返回的最多结果数。
            keyword：可选精确关键词筛选。
            reading_status：可选阅读状态筛选。
        返回：
            LibrarySemanticSearchResult：按语义分数排序的收藏，可能标记降级。
        异常：
            RuntimeError：未装配语义检索依赖时抛出稳定错误。
        """
        if self._vector_metadata_repository is None or self._semantic_searcher is None:  # 直接服务测试或未完成组合根时没有检索依赖。
            raise RuntimeError("文献库语义检索尚未装配")  # 由 API 层映射为安全服务不可用错误。
        items = self._repository.list(keyword=keyword, reading_status=reading_status, year_start=year_start, year_end=year_end, venue=venue)  # 先执行文献库结构化筛选，避免无关论文进入语义结果。
        await self._ensure_semantic_index(items)  # 仅在用户首次请求语义检索时为当前候选补齐或复用向量。
        result = await self._semantic_searcher.search(query, items, self._vector_metadata_repository, top_k)  # 在筛选集合内执行 BGE、FAISS 和 SQLite 映射过滤。
        logger.info("文献库自然语言检索完成：候选数=%d，返回数=%d，降级=%s", len(items), result.total, result.degraded)  # 仅记录数量和降级状态，不记录查询或论文正文。
        return result  # 返回稳定 API 响应模型。

    async def _ensure_semantic_index(self, items: list[LibraryItem]) -> None:
        """在首次语义检索前补齐候选论文向量，避免收藏操作等待本地模型加载。"""
        if not items or self._vector_metadata_repository is None or self._paper_indexer is None:  # 空候选、未装配索引依赖时无需额外工作。
            return  # 保持空库和显式降级场景的检索路径稳定。
        index_async = getattr(self._paper_indexer, "index_async", None)  # 读取异步索引入口，兼容历史测试替身。
        if not callable(index_async):  # 历史替身只有同步入口时不能在现有事件循环中安全调用 asyncio.run。
            logger.warning("文献库延迟语义索引跳过：索引器不支持异步写入")  # 继续执行检索器，由其决定是否降级。
            return  # 不让兼容性问题阻塞用户的语义检索请求。
        successful_count = 0  # 统计已写入或复用向量的候选数量，不记录论文身份。
        for item in items:  # 逐篇处理，复用索引器内部的 active、pending 和失败降级边界。
            index_result = await index_async(item.paper, self._vector_metadata_repository)  # 在同一请求会话中安全访问 SQLite 元数据。
            successful_count += int(index_result.indexed)  # 仅累计可供语义检索使用的向量。
        logger.info("文献库延迟语义索引完成：候选数=%d，可用向量数=%d", len(items), successful_count)  # 记录首次检索带来的模型工作量，不记录查询或论文内容。

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
        logger.info("文献库记录更新完成：更新字段数=%d", len(changes))  # 不记录关键词、备注或论文正文。
        return item  # 返回更新后的完整记录。

    def delete(self, item_id: str) -> None:
        """删除指定收藏，不存在时抛出稳定业务异常。"""
        if not self._repository.delete(item_id):  # 执行删除并检查目标是否存在。
            raise LibraryItemNotFoundError("文献库记录不存在")  # 交由 API 映射为 404。
        logger.info("文献库记录删除完成")  # 不记录用户或论文标识。

    @staticmethod
    def _build_keyword_facets(items: list[LibraryItem]) -> list[LibraryKeywordFacet]:
        """合并用户维护和来源提供的关键词，生成有稳定顺序的可点击筛选项。"""
        display_by_key: dict[str, str] = {}  # 保存大小写无关键对应的首次显示文本。
        counts: dict[str, int] = {}  # 保存每个关键词命中的不同收藏数量。
        for item in items:  # 逐条收藏统计，避免同一论文重复关键词抬高数量。
            item_keys = {value.strip().casefold(): value.strip() for value in [*item.keywords, *item.paper.keywords] if value and value.strip()}  # 合并用户和来源关键词并在单篇内去重。
            for key, display in item_keys.items():  # 累计当前论文包含的每个关键词。
                display_by_key.setdefault(key, display)  # 保留首次出现的友好显示文本。
                counts[key] = counts.get(key, 0) + 1  # 每篇论文对该关键词只贡献一次。
        facets = [LibraryKeywordFacet(keyword=display_by_key[key], count=count) for key, count in counts.items()]  # 转换为稳定公共响应对象。
        return sorted(facets, key=lambda facet: (-facet.count, facet.keyword.casefold()))[:80]  # 优先展示高频关键词并限制面板体积。
