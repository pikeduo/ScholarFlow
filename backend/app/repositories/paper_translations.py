"""持久化已保存论文的字段级中文译文缓存。"""

from datetime import datetime, timezone  # 记录缓存写入和更新的统一 UTC 时间。

from sqlalchemy import DateTime, String, Text, select  # 声明译文缓存表字段和精确读取语句。
from sqlalchemy.orm import Mapped, Session, mapped_column  # 声明 ORM 映射和调用方管理的会话类型。

from backend.app.models.paper_translation import PaperTranslationResponse  # 读写稳定的公开翻译响应契约。
from backend.app.repositories.database import Base  # 注册到统一 SQLite 元数据。


class PaperTranslationRow(Base):
    """映射以论文、字段和原文哈希唯一标识的 SQLite 译文缓存。"""

    __tablename__ = "paper_translations"  # 使用独立表避免改写搜索结果快照。

    paper_id: Mapped[str] = mapped_column(String(1024), primary_key=True)  # 保留可能包含来源 URL 的稳定论文标识。
    field: Mapped[str] = mapped_column(String(16), primary_key=True)  # 区分标题与摘要的独立译文。
    source_text_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # 原文变化时自然失效旧缓存。
    text_zh: Mapped[str] = mapped_column(Text)  # 保存模型返回的简体中文译文。
    model_name: Mapped[str] = mapped_column(String(200))  # 保存实际翻译模型以便页面说明。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 记录首次写入时间便于后续维护。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 记录最后一次覆盖更新时间。


class PaperTranslationRepository:
    """封装字段级译文缓存的精确读取和覆盖写入。"""

    def __init__(self, session: Session) -> None:
        """保存调用方创建并负责释放的 SQLite 会话。

        参数：
            session：本次缓存读取或写入使用的独立数据库会话。
        """
        self._session = session  # 避免仓储跨请求共享事务或连接。

    def get(self, paper_id: str, field: str, source_text_hash: str) -> PaperTranslationResponse | None:
        """读取与当前论文原文完全匹配的单字段译文。

        参数：
            paper_id：已保存论文的稳定标识。
            field：标题或摘要字段。
            source_text_hash：当前字段原文的 SHA-256 哈希。
        返回：
            PaperTranslationResponse | None：命中缓存时的译文，未命中时为空。
        """
        statement = select(PaperTranslationRow).where(PaperTranslationRow.paper_id == paper_id, PaperTranslationRow.field == field, PaperTranslationRow.source_text_hash == source_text_hash)  # 只按完整缓存键读取，禁止模糊匹配旧文本。
        row = self._session.scalar(statement)  # 读取至多一条由复合主键保证唯一的缓存记录。
        if row is None:  # 当前论文、字段或原文版本尚未翻译。
            return None  # 让上层按需调用模型。
        return PaperTranslationResponse(paper_id=row.paper_id, field=row.field, text_zh=row.text_zh, model_name=row.model_name)  # 恢复与 API 相同的公开响应模型。

    def save(self, translation: PaperTranslationResponse, source_text_hash: str) -> PaperTranslationResponse:
        """原子保存或覆盖当前原文版本的单字段译文。

        参数：
            translation：已由模型和响应模型校验的译文。
            source_text_hash：产生该译文的字段原文 SHA-256 哈希。
        返回：
            PaperTranslationResponse：与已提交缓存一致的公开译文。
        """
        cache_key = (translation.paper_id, translation.field, source_text_hash)  # 组合稳定复合主键供精确覆盖使用。
        row = self._session.get(PaperTranslationRow, cache_key)  # 检查同一原文版本是否已被并发或重试写入。
        now = datetime.now(timezone.utc)  # 为本次写入生成统一 UTC 时间。
        if row is None:  # 首次翻译该原文版本时创建新缓存行。
            row = PaperTranslationRow(paper_id=translation.paper_id, field=translation.field, source_text_hash=source_text_hash, text_zh=translation.text_zh, model_name=translation.model_name, created_at=now, updated_at=now)  # 只保存可展示的译文和必要溯源元数据。
            self._session.add(row)  # 加入当前事务等待原子提交。
        else:  # 模型重试或模型更新时覆盖同一原文版本的译文。
            row.text_zh = translation.text_zh  # 保持缓存结果与最后一次成功翻译一致。
            row.model_name = translation.model_name  # 同步实际翻译模型说明。
            row.updated_at = now  # 记录覆盖更新时间。
        self._session.commit()  # 确保 API 成功返回前缓存已持久化。
        return translation  # 返回不含 ORM 实现细节的稳定公共模型。
