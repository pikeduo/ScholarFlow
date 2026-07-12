"""封装 SQLite 引擎、会话工厂和基础元数据初始化。"""

from pathlib import Path  # 创建 SQLite 文件所在目录。

from sqlalchemy import create_engine  # 创建 SQLAlchemy 数据库引擎。
from sqlalchemy.engine import Engine  # 标注数据库引擎返回类型。
from sqlalchemy.orm import DeclarativeBase, sessionmaker  # 定义模型基类与会话工厂。

from backend.app.core.config import settings  # 读取数据库地址配置。
from backend.app.core.logging import logger  # 记录数据库初始化状态。


def _prepare_sqlite_directory(database_url: str) -> None:
    """在使用相对 SQLite 文件时创建其父目录。

    参数：
        database_url：SQLAlchemy 数据库连接地址。
    """
    sqlite_prefix = "sqlite:///"  # 定义文件型 SQLite URL 的固定前缀。
    if not database_url.startswith(sqlite_prefix):  # 非 SQLite 或内存数据库无需创建目录。
        return  # 交给后续其他数据库适配器处理。
    database_path = database_url.removeprefix(sqlite_prefix)  # 提取 SQLite 文件路径部分。
    if database_path == ":memory:":  # 内存数据库不需要文件目录。
        return  # 直接结束目录准备。
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)  # 创建数据库父目录。


def create_database_engine() -> Engine:
    """根据当前配置创建数据库引擎。

    返回：
        Engine：连接 SQLite 的 SQLAlchemy 引擎。
    """
    _prepare_sqlite_directory(settings.database_url)  # 确保默认数据库目录存在。
    sqlite_options = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}  # 适配 FastAPI 线程访问。
    return create_engine(settings.database_url, connect_args=sqlite_options)  # 创建延迟连接的数据库引擎。


engine = create_database_engine()  # 创建全项目复用的数据库引擎。
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)  # 提供显式事务控制的会话工厂。


class Base(DeclarativeBase):
    """所有 SQLite 业务持久化模型共享的声明式基类。"""


def initialize_database() -> None:
    """创建已注册模型对应的表，并记录基础设施准备结果。

    异常：
        SQLAlchemyError：由 SQLAlchemy 在建库或建表失败时抛出。
    """
    from backend.app.repositories import library as _library_repository  # 导入业务 ORM 映射以注册文献库表。

    _ = _library_repository  # 明确该导入用于 SQLAlchemy 元数据注册而非直接调用。
    Base.metadata.create_all(bind=engine)  # 仅创建不存在的表，不删除或修改已有数据。
    logger.info("SQLite 基础结构已准备完成，数据库=%s", settings.database_url)  # 记录数据库准备完成信息。
