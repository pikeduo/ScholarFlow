"""聚合对外暴露的版本化 API 路由。"""

from fastapi import APIRouter  # 提供路由分组能力。

from backend.app.api.routes.health import router as health_router  # 导入基础健康检查路由。


api_router = APIRouter()  # 创建后续业务模块共享的路由容器。
api_router.include_router(health_router, tags=["系统"])  # 为健康检查添加统一文档标签。
