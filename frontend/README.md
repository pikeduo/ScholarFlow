# ScholarFlow 前端

本目录包含 Vue 3 + Vite 文献搜索页面，已接入后端多源检索接口，可展示检索阶段统计、论文核验状态、证据和推荐理由。

由用户在本目录执行以下命令安装和启动：

```powershell
npm install
npm run dev
```

启动前请在仓库根目录另一个终端运行后端：

```powershell
uvicorn backend.app.main:app --reload
```

开发服务器会将 `/api` 请求代理到 `http://127.0.0.1:8000`；如后端地址不同，可设置 `SCHOLARFLOW_BACKEND_URL`。

使用 Node 内置测试器验证前端请求契约：

```powershell
npm test
```
