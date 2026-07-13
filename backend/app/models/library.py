"""定义个人文献库保存、更新、筛选与响应契约。"""

from datetime import datetime  # 保存收藏与最近更新时间。
from typing import Literal  # 限制阅读状态为稳定枚举。

from pydantic import AliasChoices, BaseModel, Field, field_validator  # 校验收藏操作和公共响应。

from backend.app.models.paper import PaperRecord  # 保存完整、可重建的论文元数据快照。


ReadingStatus = Literal["unread", "reading", "read"]  # 标记未读、阅读中和已读状态。


class SaveLibraryItemRequest(BaseModel):
    """描述保存论文到个人文献库的请求。"""

    paper: PaperRecord  # 保存来自检索结果的统一论文记录。
    keywords: list[str] = Field(default_factory=list, max_length=30, validation_alias=AliasChoices("keywords", "tags"), serialization_alias="keywords")  # 新接口统一使用关键词，兼容旧标签请求。
    note: str | None = Field(default=None, max_length=5000)  # 保存可选用户备注。
    reading_status: ReadingStatus = "unread"  # 新收藏默认标记为未读。

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, value: list[str]) -> list[str]:
        """清理空关键词并进行大小写无关去重。"""
        return _normalize_keywords(value)  # 复用稳定关键词规范化规则。


class UpdateLibraryItemRequest(BaseModel):
    """描述用户可修改的收藏属性。"""

    keywords: list[str] | None = Field(default=None, max_length=30, validation_alias=AliasChoices("keywords", "tags"), serialization_alias="keywords")  # 新接口统一使用关键词，兼容旧标签请求。
    note: str | None = Field(default=None, max_length=5000)  # 允许更新或清空备注。
    reading_status: ReadingStatus | None = None  # 允许更新阅读状态。

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, value: list[str] | None) -> list[str] | None:
        """在提供关键词时清理空值和重复项。"""
        return _normalize_keywords(value) if value is not None else None  # 未提交关键词时保持空值。


class LibraryItem(BaseModel):
    """保存一条可供前端文献库展示的收藏记录。"""

    item_id: str  # 保存文献库内部稳定标识。
    paper: PaperRecord  # 保存最近一次收藏或检索得到的论文快照。
    keywords: list[str] = Field(default_factory=list)  # 保存规范化的用户关键词。
    tags: list[str] = Field(default_factory=list, deprecated=True)  # 暂时镜像旧响应字段，供外部调用方平滑迁移到 keywords。
    note: str | None = None  # 保存可选个人备注。
    reading_status: ReadingStatus  # 保存当前阅读状态。
    saved_at: datetime  # 保存首次收藏时间。
    updated_at: datetime  # 保存最近一次修改时间。


class LibrarySaveResult(BaseModel):
    """说明保存操作是新建收藏还是命中已有论文。"""

    item: LibraryItem  # 返回保存后的完整收藏记录。
    created: bool  # 新建记录为真，去重命中已有记录为假。


class LibraryItemList(BaseModel):
    """保存筛选后的文献库列表及总数。"""

    items: list[LibraryItem] = Field(default_factory=list)  # 按最近更新时间倒序返回收藏。
    total: int = Field(default=0, ge=0)  # 返回当前筛选条件下的记录总数。
    keyword_facets: list["LibraryKeywordFacet"] = Field(default_factory=list)  # 返回当前阅读状态范围内可选关键词及命中数量。


class LibraryKeywordFacet(BaseModel):
    """描述一个可点击筛选的文献库关键词及其命中数量。"""

    keyword: str = Field(min_length=1, max_length=200)  # 保存来源或用户维护的原始关键词显示文本。
    count: int = Field(ge=1)  # 保存该关键词命中的收藏论文数量。


class LibrarySemanticSearchItem(BaseModel):
    """保存一条文献库语义检索命中及其可解释相似度分数。"""

    item: LibraryItem  # 返回与普通文献库列表一致的收藏详情。
    semantic_score: float = Field(ge=0.0, le=1.0)  # 返回归一化向量内积或降级词项匹配分数。


class LibrarySemanticSearchResult(BaseModel):
    """保存文献库自然语言语义检索结果与降级状态。"""

    items: list[LibrarySemanticSearchItem] = Field(default_factory=list)  # 按相似度降序返回命中的收藏。
    total: int = Field(default=0, ge=0)  # 返回当前结果数量。
    degraded: bool = False  # 标记是否因模型或索引不可用而采用本地词项匹配。
    degradation_reason: str | None = None  # 返回不泄露内部路径的安全降级摘要。


def _normalize_keywords(keywords: list[str]) -> list[str]:
    """规范化关键词文本并保持首次出现顺序。"""
    normalized_keywords: list[str] = []  # 保存有效且去重后的关键词。
    seen: set[str] = set()  # 保存大小写无关比较键。
    for keyword in keywords:  # 按用户输入顺序处理关键词。
        normalized = keyword.strip()  # 移除首尾无意义空白。
        key = normalized.casefold()  # 使用大小写无关键去重。
        if normalized and key not in seen:  # 仅保留首次出现的非空标签。
            normalized_keywords.append(normalized)  # 保留用户首次输入的显示形式。
            seen.add(key)  # 标记当前标签已存在。
    return normalized_keywords  # 返回稳定关键词列表。
