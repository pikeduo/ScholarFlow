# ScholarFlow 后端

FastAPI 后端位于 `backend/app`，当前提供配置、日志、SQLite 初始化和健康检查骨架。

在仓库根目录安装依赖后，由用户启动：

```powershell
uvicorn backend.app.main:app --reload
```

访问 `http://127.0.0.1:8000/docs` 查看接口文档，访问 `/api/v1/health` 检查服务状态。
