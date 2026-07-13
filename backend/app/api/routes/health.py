"""提供无需外部依赖的服务健康检查接口。"""

from typing import Literal  # 限制健康状态返回值范围。

from fastapi import APIRouter  # 声明 HTTP 路由。
from pydantic import BaseModel  # 定义稳定的响应数据模型。

from backend.app.core.config import settings  # 读取应用版本展示配置。
from backend.app.repositories.redis_client import get_redis_manager  # 读取可降级 Redis 生命周期状态。


class HealthResponse(BaseModel):
    """描述健康检查响应，供前端和部署探针消费。

    属性：
        status：服务当前状态，仅在可响应请求时返回 ok。
        service：用于识别服务名称。
        version：当前后端版本号。
        redis：Redis 短期存储是否已连接、禁用或已降级。
    """

    status: Literal["ok"]  # 限制健康状态避免出现不一致文本。
    service: str  # 返回供监控系统识别的服务名。
    version: str  # 返回当前应用版本。
    redis: Literal["disabled", "available", "unavailable"]  # 返回 Redis 状态但不泄露地址或认证信息。


router = APIRouter(prefix="/health")  # 将健康检查归入固定资源路径。


@router.get("", response_model=HealthResponse, summary="检查后端服务状态")
async def read_health() -> HealthResponse:
    """返回应用可处理请求的最小状态信息。

    返回：
        HealthResponse：包含固定健康状态、服务名称、版本和 Redis 降级状态。
    """
    return HealthResponse(status="ok", service=settings.app_name, version="0.1.0", redis=get_redis_manager().status)  # 构造包含短期存储降级状态的稳定响应。
