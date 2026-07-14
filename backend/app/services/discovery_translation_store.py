"""提供 SQLite 补充网页发现译文缓存的可替换服务边界。"""

from collections.abc import Callable  # 标注测试可替换的会话工厂。
from hashlib import sha256  # 为原文版本生成不可逆且稳定的缓存键组成部分。
from typing import Literal, Protocol  # 约束字段范围并定义最小服务协议。

from sqlalchemy.exc import SQLAlchemyError  # 映射数据库故障为安全服务错误。
from sqlalchemy.orm import Session  # 标注会话工厂返回类型。

from backend.app.core.logging import logger  # 记录不含网页正文的缓存故障堆栈。
from backend.app.models.discovery_translation import DiscoveryTranslationResponse  # 传递独立网页发现翻译响应。
from backend.app.repositories.database import SessionLocal  # 默认创建独立短生命周期 SQLite 会话。
from backend.app.repositories.discovery_translations import DiscoveryTranslationRepository  # 隔离 ORM 表读写细节。


DiscoveryTranslationField = Literal["title", "snippet"]  # 统一网页发现允许翻译的两个字段范围。


class DiscoveryTranslationStore(Protocol):
    """定义网页发现翻译路由读取与写入缓存所需的最小边界。"""

    def get(self, discovery_id: str, field: DiscoveryTranslationField, source_text: str) -> DiscoveryTranslationResponse | None:
        """读取与当前原文一致的字段级缓存，未命中时返回空值。"""
        ...  # 实现不得调用模型，便于测试替换。

    def save(self, translation: DiscoveryTranslationResponse, source_text: str) -> DiscoveryTranslationResponse:
        """持久化模型生成的网页发现译文并返回已保存结果。"""
        ...  # 实现必须确保原文变化不会复用旧译文。


class DiscoveryTranslationStoreError(RuntimeError):
    """表示网页发现译文缓存无法安全读取或写入的服务边界错误。"""


class SqliteDiscoveryTranslationStore:
    """使用 SQLite 保存可跨浏览器复用的补充网页发现译文。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        """保存可测试替换的短生命周期数据库会话工厂。"""
        self._session_factory = session_factory  # 确保每个 HTTP 请求独立管理连接和事务。

    def get(self, discovery_id: str, field: DiscoveryTranslationField, source_text: str) -> DiscoveryTranslationResponse | None:
        """按网页发现、字段及当前原文哈希读取已持久化译文。"""
        session = self._session_factory()  # 为本次缓存读取创建独立会话。
        try:  # 将 ORM 和事务异常隔离在服务层。
            return DiscoveryTranslationRepository(session).get(discovery_id, field, _source_text_hash(source_text))  # 仅命中完全相同原文版本的译文。
        except SQLAlchemyError as exc:  # 不将 SQL 或数据库路径泄露给 API 调用方。
            logger.exception("网页发现译文缓存读取失败：发现=%s，字段=%s", discovery_id, field)  # 不记录标题、摘要片段或译文。
            raise DiscoveryTranslationStoreError("网页发现译文缓存暂时不可用") from exc  # 返回稳定可处理的存储错误。
        finally:  # 所有读取路径均释放 SQLite 会话。
            session.close()  # 防止多个网页卡片操作积累连接。

    def save(self, translation: DiscoveryTranslationResponse, source_text: str) -> DiscoveryTranslationResponse:
        """保存模型已生成译文，持久化失败时返回安全服务错误。"""
        session = self._session_factory()  # 为本次缓存写入创建独立会话。
        try:  # 将事务边界与路由逻辑分离。
            return DiscoveryTranslationRepository(session).save(translation, _source_text_hash(source_text))  # 以当前原文哈希写入字段级缓存。
        except SQLAlchemyError as exc:  # 缓存写入失败时不得留下半提交事务。
            session.rollback()  # 回滚未完成事务以避免连接复用污染。
            logger.exception("网页发现译文缓存写入失败：发现=%s，字段=%s", translation.discovery_id, translation.field)  # 不记录原文或译文内容。
            raise DiscoveryTranslationStoreError("网页发现译文缓存暂时无法保存") from exc  # 返回可安全展示的稳定错误。
        finally:  # 成功和异常路径均释放 SQLite 会话。
            session.close()  # 防止翻译请求长期占用数据库连接。


def _source_text_hash(source_text: str) -> str:
    """返回当前字段原文的 UTF-8 SHA-256 哈希，避免将原文写入缓存键。"""
    return sha256(source_text.encode("utf-8")).hexdigest()  # 显式 UTF-8 保证跨 Windows 与服务端的一致性。
