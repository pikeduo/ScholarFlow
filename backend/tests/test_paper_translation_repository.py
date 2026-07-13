"""验证 SQLite 译文缓存的字段隔离、原文失效与覆盖写入。"""

from sqlalchemy import create_engine  # 创建不访问用户数据文件的内存 SQLite 引擎。
from sqlalchemy.orm import sessionmaker  # 为每个用例创建独立数据库会话。

from backend.app.models.paper_translation import PaperTranslationResponse  # 构造已校验的字段级翻译响应。
from backend.app.repositories.database import Base  # 创建已注册的 ORM 表。
from backend.app.repositories.paper_translations import PaperTranslationRepository  # 导入待测 SQLite 缓存仓储。


def _translation(field: str, text_zh: str) -> PaperTranslationResponse:
    """构造测试用的单字段中文译文。"""
    return PaperTranslationResponse(paper_id="https://openalex.org/W1", field=field, text_zh=text_zh, model_name="deepseek-v4-flash")  # 使用包含斜杠的来源标识验证持久化兼容性。


def test_repository_keeps_title_and_abstract_cache_entries_independent() -> None:
    """同一论文的标题和摘要缓存应独立命中，原文改变时不得复用。"""
    engine = create_engine("sqlite:///:memory:")  # 创建隔离内存库避免影响用户 SQLite 数据。
    Base.metadata.create_all(engine)  # 创建包括新译文缓存表在内的注册 ORM 表。
    session = sessionmaker(bind=engine)()  # 创建本用例的独立会话。
    try:  # 确保断言失败也会释放资源。
        repository = PaperTranslationRepository(session)  # 装配待测缓存仓储。
        repository.save(_translation("title", "标题译文"), "title-hash-v1")  # 保存第一版标题译文。
        repository.save(_translation("abstract", "摘要译文"), "abstract-hash-v1")  # 保存第一版摘要译文。

        assert repository.get("https://openalex.org/W1", "title", "title-hash-v1").text_zh == "标题译文"  # 验证标题只读取自身缓存。
        assert repository.get("https://openalex.org/W1", "abstract", "abstract-hash-v1").text_zh == "摘要译文"  # 验证摘要只读取自身缓存。
        assert repository.get("https://openalex.org/W1", "title", "title-hash-v2") is None  # 验证原文版本变化时旧标题译文不会复用。
    finally:  # 无论用例结果如何都释放内存数据库资源。
        session.close()  # 关闭测试会话。
        engine.dispose()  # 释放内存引擎。
