"""编排个人文献库操作并记录不含用户备注的安全统计。"""

from backend.app.core.logging import logger  # 记录收藏数量和操作类型等非敏感统计。
from backend.app.models.library import LibraryItem, LibraryItemList, LibrarySaveResult, ReadingStatus, SaveLibraryItemRequest, UpdateLibraryItemRequest  # 接收稳定请求并返回公共领域模型。
from backend.app.repositories.library import LibraryRepository  # 依赖可替换 SQLite 仓储。


class LibraryItemNotFoundError(LookupError):
    """表示指定的个人文献库记录不存在。"""


class LibraryService:
    """提供去重收藏、筛选、属性更新和删除的业务边界。"""

    def __init__(self, repository: LibraryRepository) -> None:
        """保存由 API 或测试注入的文献库仓储。"""
        self._repository = repository  # 服务不依赖全局数据库会话。

    def save(self, request: SaveLibraryItemRequest) -> LibrarySaveResult:
        """保存论文，并明确返回本次是否创建新收藏。"""
        item, created = self._repository.save(request.paper, request.tags, request.note, request.reading_status)  # 执行身份去重与原子写入。
        logger.info("文献库保存完成：新建=%s，标签数=%d", created, len(item.tags))  # 不记录标题、备注或完整论文内容。
        return LibrarySaveResult(item=item, created=created)  # 返回稳定保存结果。

    def list(self, tag: str | None = None, reading_status: ReadingStatus | None = None) -> LibraryItemList:
        """按可选标签和阅读状态返回收藏列表。"""
        items = self._repository.list(tag=tag, reading_status=reading_status)  # 执行确定性筛选。
        logger.info("文献库查询完成：结果数=%d，按标签筛选=%s，按状态筛选=%s", len(items), bool(tag), reading_status is not None)  # 只记录筛选是否启用。
        return LibraryItemList(items=items, total=len(items))  # 返回当前筛选集合和总数。

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
