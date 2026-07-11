# ScholarFlow 后端

FastAPI 后端位于 `backend/app`，当前提供配置、日志、SQLite 初始化和健康检查骨架。

在仓库根目录安装依赖后，由用户启动：

```powershell
uvicorn backend.app.main:app --reload
```

应用日志、Uvicorn 启动/异常日志和 HTTP 访问日志会同时输出到控制台，并写入仓库根目录的 `logs/scholarflow.log`。日志按 5 MiB 滚动，最多保留五个历史文件，`logs/` 已被 Git 忽略。

在 PowerShell 中持续查看最新运行信息：

```powershell
Get-Content -Encoding UTF8 -Wait logs/scholarflow.log
```

访问 `http://127.0.0.1:8000/docs` 查看接口文档，访问 `/api/v1/health` 检查服务状态。
