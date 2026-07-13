"""使用 SQLite 持久化个人文献库论文快照与用户属性。"""

import json  # 以 UTF-8 JSON 文本保存嵌套论文和标签结构。
from datetime import datetime, timezone  # 生成无歧义 UTC 时间戳。
from uuid import uuid4  # 生成不依赖数据库序列的收藏标识。

from sqlalchemy import DateTime, String, Text, select  # 声明表字段并构造筛选查询。
from sqlalchemy.orm import Mapped, Session, mapped_column  # 声明 ORM 映射并执行显式事务。

from backend.app.models.library import LibraryItem, LibrarySort, ReadingStatus  # 返回稳定领域响应并约束列表排序策略。
from backend.app.models.paper import PaperRecord  # 序列化与恢复完整论文快照。
from backend.app.repositories.database import Base  # 注册到统一 SQLAlchemy 元数据。


class LibraryItemRow(Base):
    """映射 SQLite 中的个人文献库记录。"""

    __tablename__ = "library_items"  # 使用稳定表名供未来迁移复用。

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True)  # 保存 UUID 文本主键。
    identity_key: Mapped[str] = mapped_column(String(1024), unique=True, index=True)  # 保存按论文身份优先级生成的唯一去重键。
    paper_json: Mapped[str] = mapped_column(Text)  # 保存完整 PaperRecord JSON 快照。
    tags_json: Mapped[str] = mapped_column(Text, default="[]")  # 沿用历史列名保存规范化用户关键词数组。
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 保存可选个人备注。
    reading_status: Mapped[str] = mapped_column(String(16), default="unread")  # 保存受领域契约限制的阅读状态。
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 保存首次收藏 UTC 时间。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 保存最近修改 UTC 时间。


class LibraryRepository:
    """封装文献库的去重保存、筛选、更新和删除事务。"""

    def __init__(self, session: Session) -> None:
        """保存由 API 或测试注入的数据库会话。"""
        self._session = session  # 每次请求使用独立会话避免跨请求事务污染。

    def save(self, paper: PaperRecord, keywords: list[str], note: str | None, reading_status: ReadingStatus) -> tuple[LibraryItem, bool]:
        """按稳定论文身份去重保存，并在重复时合并关键词、刷新快照。"""
        identity_key = build_library_identity_key(paper)  # 生成唯一论文身份键。
        row = self._session.scalar(select(LibraryItemRow).where(LibraryItemRow.identity_key == identity_key))  # 查询是否已收藏同一论文。
        now = datetime.now(timezone.utc)  # 为本次写入生成统一 UTC 时间。
        created = row is None  # 记录本次操作是否实际插入新记录。
        if row is None:  # 首次收藏时创建完整记录。
            row = LibraryItemRow(item_id=str(uuid4()), identity_key=identity_key, paper_json=_dump_paper(paper), tags_json=_dump_keywords(keywords), note=note, reading_status=reading_status, saved_at=now, updated_at=now)  # 构造待插入行。
            self._session.add(row)  # 将新记录加入当前事务。
        else:  # 重复收藏时保留首次收藏时间并更新可变内容。
            existing_keywords = _load_keywords(row.tags_json)  # 读取已有关键词供无损合并。
            row.paper_json = _dump_paper(paper)  # 使用最新检索元数据刷新论文快照。
            row.tags_json = _dump_keywords(_merge_keywords(existing_keywords, keywords))  # 合并新旧关键词且保持顺序。
            row.note = note if note is not None else row.note  # 仅在新请求提供备注时覆盖旧备注。
            row.reading_status = reading_status if reading_status != "unread" else row.reading_status  # 默认未读不应意外覆盖已有进度。
            row.updated_at = now  # 更新最近修改时间。
        self._session.commit()  # 原子提交插入或更新操作。
        self._session.refresh(row)  # 读取数据库最终值供响应使用。
        return _to_library_item(row), created  # 返回领域模型和新建标记。

    def list(self, keyword: str | None = None, reading_status: ReadingStatus | None = None, year_start: int | None = None, year_end: int | None = None, venue: str | None = None, sort: LibrarySort = "updated_desc") -> list[LibraryItem]:
        """按可选关键词和阅读状态筛选收藏，并按最近更新倒序返回。"""
        statement = select(LibraryItemRow)  # 从完整文献库集合开始构造查询。
        if reading_status is not None:  # 阅读状态可在 SQLite 中直接精确筛选。
            statement = statement.where(LibraryItemRow.reading_status == reading_status)  # 添加状态条件。
        rows = list(self._session.scalars(statement.order_by(LibraryItemRow.updated_at.desc(), LibraryItemRow.item_id)).all())  # 保持稳定倒序。
        if keyword is not None:  # JSON 论文快照和用户关键词使用内存精确筛选，避免 SQLite 模糊匹配误命中。
            keyword_key = keyword.strip().casefold()  # 规范化查询关键词。
            rows = [row for row in rows if keyword_key in _keyword_keys(row)]  # 同时匹配用户维护和来源提供的关键词。
        items = [_to_library_item(row) for row in rows]  # 将 ORM 行转换为可安全读取的公共领域模型。
        if year_start is not None:  # 仅保留存在年份且不早于起始年的论文。
            items = [item for item in items if item.paper.year is not None and item.paper.year >= year_start]  # 缺失年份不能满足显式年份下限。
        if year_end is not None:  # 仅保留存在年份且不晚于结束年的论文。
            items = [item for item in items if item.paper.year is not None and item.paper.year <= year_end]  # 缺失年份不能满足显式年份上限。
        if venue is not None:  # 在来源期刊或会议名称中执行大小写无关的包含匹配。
            venue_key = venue.strip().casefold()  # 规范化用户输入以保持匹配稳定。
            items = [item for item in items if venue_key in (item.paper.venue or "").casefold()]  # 空 venue 不匹配显式筛选。
        return _sort_library_items(items, sort)  # 统一在筛选完成后应用用户选择的展示排序。

    def get(self, item_id: str) -> LibraryItem | None:
        """按内部收藏标识读取单条记录。"""
        row = self._session.get(LibraryItemRow, item_id)  # 使用主键高效查找记录。
        return _to_library_item(row) if row is not None else None  # 不存在时返回空值交由服务映射。

    def find_paper(self, paper_id: str) -> PaperRecord | None:
        """按论文内部标识读取文献库快照，不触发任何外部学术来源调用。"""
        rows = self._session.scalars(select(LibraryItemRow)).all()  # 文献库为个人小集合，使用确定性本地扫描避免 JSON 模糊查询误命中。
        for row in rows:  # 逐条恢复已保存快照并比较稳定论文标识。
            paper = PaperRecord.model_validate_json(row.paper_json)  # 通过统一模型校验历史快照结构。
            if paper.paper_id == paper_id:  # 仅返回与请求标识完全一致的已收藏论文。
                return paper  # 命中后立即结束扫描。
        return None  # 未收藏或标识不一致时保持安全空结果。

    def update(self, item_id: str, changes: dict[str, object]) -> LibraryItem | None:
        """只更新请求明确提交的关键词、备注或阅读状态。"""
        row = self._session.get(LibraryItemRow, item_id)  # 定位待更新收藏。
        if row is None:  # 不存在时不创建隐式记录。
            return None  # 交由 API 返回稳定 404。
        if "keywords" in changes:  # 允许明确提交空列表以清除关键词。
            row.tags_json = _dump_keywords(changes["keywords"] if isinstance(changes["keywords"], list) else [])  # 保存已由请求模型校验的关键词。
        if "note" in changes:  # 允许明确提交 null 清空备注。
            row.note = changes["note"] if isinstance(changes["note"], str) else None  # 保存文本或空值。
        if "reading_status" in changes and isinstance(changes["reading_status"], str):  # 仅处理已校验状态文本。
            row.reading_status = changes["reading_status"]  # 更新阅读状态。
        row.updated_at = datetime.now(timezone.utc)  # 标记最近修改时间。
        self._session.commit()  # 原子提交属性更新。
        self._session.refresh(row)  # 读取最终数据库状态。
        return _to_library_item(row)  # 返回更新后的完整记录。

    def delete(self, item_id: str) -> bool:
        """删除指定收藏并返回其是否曾存在。"""
        row = self._session.get(LibraryItemRow, item_id)  # 查找目标记录。
        if row is None:  # 不存在时不执行空事务。
            return False  # 供 API 返回稳定 404。
        self._session.delete(row)  # 标记记录删除。
        self._session.commit()  # 提交删除事务。
        return True  # 报告删除成功。


def build_library_identity_key(paper: PaperRecord) -> str:
    """按 DOI、专业标识和内部 ID 优先级生成稳定去重键。"""
    identifiers = [  # 将最可靠的跨来源标识放在前面。
        ("doi", _normalize_doi(paper.doi)),  # DOI 为最高优先级身份。
        ("arxiv", _normalize_identifier(paper.arxiv_id)),  # 其次使用 arXiv 标识。
        ("pmid", _normalize_identifier(paper.pmid)),  # 医学论文使用 PMID。
        ("openalex", _normalize_identifier(paper.openalex_id)),  # 使用 OpenAlex 平台标识。
        ("semantic_scholar", _normalize_identifier(paper.semantic_scholar_id)),  # 使用 Semantic Scholar 标识。
        ("dblp", _normalize_identifier(paper.dblp_key)),  # 使用 DBLP key。
        ("paper", _normalize_identifier(paper.paper_id)),  # 最后回退到统一论文内部标识。
    ]
    for namespace, value in identifiers:  # 按可信度顺序选择首个有效标识。
        if value:  # 忽略来源未提供的标识。
            return f"{namespace}:{value}"  # 带命名空间避免不同来源 ID 碰撞。
    raise ValueError("论文缺少可用于收藏去重的稳定标识")  # PaperRecord 理论上已有 paper_id，此处防御异常对象。


def _normalize_doi(value: str | None) -> str:
    """移除 DOI URL 和前缀并统一大小写。"""
    normalized = _normalize_identifier(value)  # 先清理空白和大小写。
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):  # 兼容常见 DOI 表示形式。
        if normalized.startswith(prefix):  # 只移除开头前缀。
            return normalized.removeprefix(prefix).strip()  # 返回 DOI 主体。
    return normalized  # 已是主体时直接返回。


def _normalize_identifier(value: str | None) -> str:
    """将可选标识清理为大小写无关比较值。"""
    return value.strip().casefold() if value else ""  # 空值稳定归为空字符串。


def _dump_paper(paper: PaperRecord) -> str:
    """将论文快照编码为可读 UTF-8 JSON 文本。"""
    return paper.model_dump_json(exclude_none=False)  # Pydantic 负责 datetime 等字段的稳定编码。


def _dump_keywords(keywords: list[str]) -> str:
    """将用户关键词数组编码为不转义中文的 JSON。"""
    return json.dumps(keywords, ensure_ascii=False)  # 保持 SQLite 调试时中文可读。


def _load_keywords(value: str) -> list[str]:
    """从数据库历史 tags_json 列恢复用户关键词数组。"""
    parsed = json.loads(value)  # 数据由本仓储写入，可按稳定 JSON 解析。
    return [str(item) for item in parsed] if isinstance(parsed, list) else []  # 防御异常历史数据形状。


def _merge_keywords(first: list[str], second: list[str]) -> list[str]:
    """大小写无关合并新旧关键词并保持首次顺序。"""
    merged: list[str] = []  # 保存合并后的标签。
    seen: set[str] = set()  # 保存规范化比较键。
    for keyword in [*first, *second]:  # 已有关键词在前，新关键词在后。
        key = keyword.casefold()  # 使用大小写无关键。
        if key not in seen:  # 只保留首次出现的标签。
            merged.append(keyword)  # 保存原显示形式。
            seen.add(key)  # 标记已出现。
    return merged  # 返回稳定关键词列表。


def _keyword_keys(row: LibraryItemRow) -> set[str]:
    """合并用户关键词和论文来源关键词，供精确关键词筛选使用。"""
    paper = PaperRecord.model_validate_json(row.paper_json)  # 读取保存的论文快照以获得来源关键词。
    return {value.strip().casefold() for value in [*_load_keywords(row.tags_json), *paper.keywords] if value and value.strip()}  # 统一清理并返回大小写无关键集合。


def _sort_library_items(items: list[LibraryItem], sort: LibrarySort) -> list[LibraryItem]:
    """按照公开排序契约排序收藏，始终为缺失年份保留确定性末位。"""
    if sort == "year_desc":  # 年份倒序时优先较新的论文，缺失年份固定排在末尾。
        return sorted(items, key=lambda item: (item.paper.year is not None, item.paper.year or -1, item.updated_at, item.item_id), reverse=True)  # 使用更新时间和内部 ID 消除同年并列的不稳定性。
    if sort == "year_asc":  # 年份正序时优先较早的论文，缺失年份固定排在末尾。
        return sorted(items, key=lambda item: (item.paper.year is None, item.paper.year or 9999, item.updated_at, item.item_id))  # 使用更新时间和内部 ID 保持排序可复现。
    if sort == "title_asc":  # 标题排序采用大小写无关的标题文本。
        return sorted(items, key=lambda item: ((item.paper.title or "").casefold(), item.updated_at, item.item_id))  # 空标题和并列标题仍具备稳定次级顺序。
    return sorted(items, key=lambda item: (item.updated_at, item.item_id), reverse=True)  # 默认按最近更新倒序展示。


def _to_library_item(row: LibraryItemRow) -> LibraryItem:
    """将持久化行转换为经过校验的公共领域模型。"""
    keywords = _load_keywords(row.tags_json)  # 从历史列读取规范化的用户关键词。
    return LibraryItem(item_id=row.item_id, paper=PaperRecord.model_validate_json(row.paper_json), keywords=keywords, tags=keywords, note=row.note, reading_status=row.reading_status, saved_at=row.saved_at, updated_at=row.updated_at)  # 同时提供新关键词字段和旧标签镜像以兼容外部调用方。
