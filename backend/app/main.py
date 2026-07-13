"""FastAPI 应用入口，负责组装路由与基础设施生命周期。"""

from contextlib import asynccontextmanager  # 管理应用启动和关闭阶段。
from collections.abc import AsyncIterator  # 标注异步生命周期迭代器。

from fastapi import FastAPI  # 提供 ASGI Web 应用框架。
from sqlalchemy.exc import SQLAlchemyError  # 捕获数据库初始化异常。

from backend.app.api.router import api_router  # 导入版本化 API 路由聚合器。
from backend.app.core.config import settings  # 读取集中式应用配置。
from backend.app.core.logging import logger  # 使用统一控制台和文件日志器。
from backend.app.repositories.database import initialize_database  # 初始化 SQLite 元数据。
from backend.app.repositories.redis_client import get_redis_manager  # 管理可选 Redis 短期存储生命周期。


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """管理应用生命周期，并在启动时准备本地数据库。

    参数：
        _: FastAPI 应用实例，此处不需要直接访问。
    返回：
        让 FastAPI 继续处理请求的异步上下文。
    异常：
        SQLAlchemyError：数据库初始化失败时向上抛出以阻止异常启动。
    """
    logger.info("正在启动 ScholarFlow 后端，环境=%s", settings.environment)  # 记录阶段性启动信息。
    try:  # 将基础设施错误记录为完整堆栈。
        initialize_database()  # 创建尚不存在的 SQLite 数据库与表结构。
    except SQLAlchemyError:  # 仅处理数据库层可预期异常。
        logger.exception("SQLite 初始化失败，应用停止启动")  # 记录完整错误堆栈便于排查。
        raise  # 保持失败可见，避免服务带着不可用状态运行。
    await get_redis_manager().start()  # Redis 不可用时内部降级，SQLite 主服务仍可继续启动。
    yield  # 将控制权交还给 FastAPI 请求处理流程。
    await get_redis_manager().close()  # 在应用关闭时释放 Redis 连接池。
    logger.info("ScholarFlow 后端已停止")  # 记录正常关闭信息。


app = FastAPI(  # 创建唯一的 ASGI 应用实例。
    title=settings.app_name,  # 在 OpenAPI 文档中显示项目名称。
    version="0.1.0",  # 标注当前基础工程版本。
    lifespan=lifespan,  # 注册数据库与日志生命周期。
)
app.include_router(api_router, prefix=settings.api_v1_prefix)  # 挂载所有版本化 API 路由。
