"""持久化补充网页发现的字段级中文译文缓存。"""

from datetime import datetime, timezone  # 记录缓存写入和更新的统一 UTC 时间。

from sqlalchemy import DateTime, String, Text, select  # 声明 SQLite 字段和精确读取语句。
from sqlalchemy.orm import Mapped, Session, mapped_column  # 声明 ORM 映射和会话类型。

from backend.app.models.discovery_translation import DiscoveryTranslationResponse  # 读写独立于论文的网页发现翻译契约。
from backend.app.repositories.database import Base  # 注册到统一 SQLite 元数据。


class DiscoveryTranslationRow(Base):
    """映射以网页发现、字段和原文哈希唯一标识的 SQLite 译文缓存。"""

    __tablename__ = "discovery_translations"  # 使用独立表，避免将网页发现误作论文数据。

    discovery_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # 保存由来源和 URL 派生的不可读缓存标识。
    field: Mapped[str] = mapped_column(String(16), primary_key=True)  # 区分标题与摘要片段的独立译文。
    source_text_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # 原文变化时自然失效旧译文。
    text_zh: Mapped[str] = mapped_column(Text)  # 保存 DeepSeek 返回的简体中文译文。
    model_name: Mapped[str] = mapped_column(String(200))  # 保存实际翻译模型以便展示与审计。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 记录首次写入时间。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 记录最后一次覆盖时间。


class DiscoveryTranslationRepository:
    """封装补充网页发现译文缓存的精确读取与覆盖写入。"""

    def __init__(self, session: Session) -> None:
        """保存调用方创建并负责释放的 SQLite 会话。"""
        self._session = session  # 避免仓储跨请求共享事务或连接。

    def get(self, discovery_id: str, field: str, source_text_hash: str) -> DiscoveryTranslationResponse | None:
        """读取与当前网页发现原文完全匹配的单字段译文。"""
        statement = select(DiscoveryTranslationRow).where(DiscoveryTranslationRow.discovery_id == discovery_id, DiscoveryTranslationRow.field == field, DiscoveryTranslationRow.source_text_hash == source_text_hash)  # 只按完整缓存键读取，禁止模糊复用旧文本。
        row = self._session.scalar(statement)  # 读取至多一条由复合主键保证唯一的缓存记录。
        if row is None:  # 当前字段尚未翻译或原文已变化。
            return None  # 让上层在用户主动操作后调用模型。
        return DiscoveryTranslationResponse(discovery_id=row.discovery_id, field=row.field, text_zh=row.text_zh, model_name=row.model_name)  # 恢复独立网页发现公开响应。

    def save(self, translation: DiscoveryTranslationResponse, source_text_hash: str) -> DiscoveryTranslationResponse:
        """原子保存或覆盖当前原文版本的单字段译文。"""
        cache_key = (translation.discovery_id, translation.field, source_text_hash)  # 组合稳定复合主键供精确覆盖使用。
        row = self._session.get(DiscoveryTranslationRow, cache_key)  # 检查同一原文是否已由并发请求写入。
        now = datetime.now(timezone.utc)  # 为本次写入生成统一 UTC 时间。
        if row is None:  # 首次翻译该原文版本时创建缓存行。
            row = DiscoveryTranslationRow(discovery_id=translation.discovery_id, field=translation.field, source_text_hash=source_text_hash, text_zh=translation.text_zh, model_name=translation.model_name, created_at=now, updated_at=now)  # 仅保存译文和必要溯源元数据。
            self._session.add(row)  # 加入当前事务等待原子提交。
        else:  # 模型重试或模型更新时覆盖同一原文版本。
            row.text_zh = translation.text_zh  # 保持缓存结果与最后一次成功翻译一致。
            row.model_name = translation.model_name  # 同步实际翻译模型说明。
            row.updated_at = now  # 记录覆盖更新时间。
        self._session.commit()  # 确保 API 成功返回前缓存已持久化。
        return translation  # 返回不含 ORM 实现细节的稳定公共模型。
