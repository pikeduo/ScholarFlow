"""聚合对外暴露的版本化 API 路由。"""

from fastapi import APIRouter  # 提供路由分组能力。

from backend.app.api.routes.health import router as health_router  # 导入基础健康检查路由。
from backend.app.api.routes.library import router as library_router  # 导入个人文献库业务路由。
from backend.app.api.routes.papers import router as papers_router  # 导入只读论文详情路由。
from backend.app.api.routes.search import router as search_router  # 导入 OpenAlex 检索路由。


api_router = APIRouter()  # 创建后续业务模块共享的路由容器。
api_router.include_router(health_router, tags=["系统"])  # 为健康检查添加统一文档标签。
api_router.include_router(library_router, tags=["文献库"])  # 为收藏、筛选、更新和删除添加统一标签。
api_router.include_router(papers_router, tags=["论文"])  # 为 SQLite 论文详情读取添加统一标签。
api_router.include_router(search_router, tags=["检索"])  # 为 OpenAlex 搜索接口添加统一文档标签。
