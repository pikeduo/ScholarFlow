"""验证基础健康检查接口的稳定响应。"""

from fastapi.testclient import TestClient  # 通过 ASGI 测试客户端调用接口。

from app.main import app  # 导入待测 FastAPI 应用实例。


def test_health_endpoint_returns_service_metadata() -> None:
    """健康检查应返回 200、服务名和当前基础工程版本。"""
    client = TestClient(app)  # 构造不依赖网络的本地测试客户端。
    response = client.get("/api/v1/health")  # 请求基础存活探针接口。
    payload = response.json()  # 解析 JSON 响应便于逐字段断言。
    assert response.status_code == 200  # 验证应用可正常处理请求。
    assert payload["status"] == "ok"  # 验证稳定的健康状态。
    assert payload["service"] == "ScholarFlow"  # 验证默认服务标识。
    assert payload["version"] == "0.1.0"  # 验证当前后端版本标记。
