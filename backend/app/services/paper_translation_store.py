"""提供 SQLite 论文译文缓存的可替换服务边界。"""

from collections.abc import Callable  # 标注可由测试替换的会话工厂。
from hashlib import sha256  # 为原文版本生成不可逆且稳定的缓存键组成部分。
from typing import Literal, Protocol  # 约束字段范围并定义路由依赖的最小协议。

from sqlalchemy.exc import SQLAlchemyError  # 映射数据库故障为安全的服务错误。
from sqlalchemy.orm import Session  # 标注会话工厂返回类型。

from backend.app.core.logging import logger  # 记录不含论文原文的完整缓存故障堆栈。
from backend.app.models.paper_translation import PaperTranslationResponse  # 传递稳定公开翻译响应。
from backend.app.repositories.database import SessionLocal  # 默认创建独立短生命周期 SQLite 会话。
from backend.app.repositories.paper_translations import PaperTranslationRepository  # 隔离 ORM 表读写细节。


TranslationField = Literal["title", "abstract"]  # 统一标题与摘要的允许字段范围。


class PaperTranslationStore(Protocol):
    """定义路由读取和写入译文缓存所需的最小边界。"""

    def get(self, paper_id: str, field: TranslationField, source_text: str) -> PaperTranslationResponse | None:
        """读取与当前原文一致的字段级缓存，未命中时返回空值。"""
        ...  # 实现可替换为 SQLite 或测试替身，但不得调用模型。

    def save(self, translation: PaperTranslationResponse, source_text: str) -> PaperTranslationResponse:
        """持久化模型生成的字段级译文并返回已保存结果。"""
        ...  # 实现必须确保原文变化不会复用旧译文。


class PaperTranslationStoreError(RuntimeError):
    """表示译文缓存无法安全读取或写入的服务边界错误。"""


class SqlitePaperTranslationStore:
    """使用 SQLite 保存可跨浏览器复用的论文译文缓存。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        """保存可测试替换的短生命周期数据库会话工厂。"""
        self._session_factory = session_factory  # 确保每个 HTTP 请求独立管理连接和事务。

    def get(self, paper_id: str, field: TranslationField, source_text: str) -> PaperTranslationResponse | None:
        """按论文、字段及当前原文哈希读取已持久化译文。

        异常：
            PaperTranslationStoreError：SQLite 无法读取时抛出。
        """
        session = self._session_factory()  # 为本次缓存读取创建独立会话。
        try:  # 将 ORM 和事务异常隔离在服务层。
            return PaperTranslationRepository(session).get(paper_id, field, _source_text_hash(source_text))  # 仅命中完全相同原文版本的译文。
        except SQLAlchemyError as exc:  # 不将 SQL 或数据库路径泄露给 API 调用方。
            logger.exception("论文译文缓存读取失败：论文=%s，字段=%s", paper_id, field)  # 不记录原文或译文内容。
            raise PaperTranslationStoreError("论文译文缓存暂时不可用") from exc  # 返回稳定可处理的存储错误。
        finally:  # 所有读取路径均释放 SQLite 会话。
            session.close()  # 防止论文列表多次操作积累连接。

    def save(self, translation: PaperTranslationResponse, source_text: str) -> PaperTranslationResponse:
        """保存模型已生成译文，持久化失败时不返回未缓存的成功结果。

        异常：
            PaperTranslationStoreError：SQLite 无法提交时抛出。
        """
        session = self._session_factory()  # 为本次缓存写入创建独立会话。
        try:  # 将事务边界与路由逻辑分离。
            return PaperTranslationRepository(session).save(translation, _source_text_hash(source_text))  # 以当前原文哈希写入字段级缓存。
        except SQLAlchemyError as exc:  # 缓存写入失败时不得留下半提交事务。
            session.rollback()  # 回滚未完成写入以避免连接复用污染。
            logger.exception("论文译文缓存写入失败：论文=%s，字段=%s", translation.paper_id, translation.field)  # 不记录公开文本内容。
            raise PaperTranslationStoreError("论文译文缓存暂时无法保存") from exc  # 返回可安全展示的稳定错误。
        finally:  # 成功和异常路径均释放会话。
            session.close()  # 防止翻译请求长期占用数据库连接。


def _source_text_hash(source_text: str) -> str:
    """返回当前字段原文的 UTF-8 SHA-256 哈希，避免将原文写入缓存键。"""
    return sha256(source_text.encode("utf-8")).hexdigest()  # 显式 UTF-8 保证跨 Windows 与服务端的一致性。
