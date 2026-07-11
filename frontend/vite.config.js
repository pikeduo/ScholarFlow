import { defineConfig } from 'vite' // 导入 Vite 配置辅助函数。
import vue from '@vitejs/plugin-vue' // 导入 Vue 单文件组件编译插件。

export default defineConfig({ // 导出前端构建工具配置。
  plugins: [vue()], // 启用 Vue 3 单文件组件支持。
  server: { // 配置本地开发服务器。
    port: 5173, // 固定默认端口以便前后端联调。
    strictPort: true, // 端口被占用时明确报错，避免误连其他应用。
    proxy: { // 在开发环境转发版本化 API，避免额外放宽后端 CORS。
      '/api': { // 仅代理后端 API 路径，不影响前端静态资源。
        target: process.env.SCHOLARFLOW_BACKEND_URL || 'http://127.0.0.1:8000', // 允许按环境覆盖本地 FastAPI 地址。
        changeOrigin: true, // 使用目标主机头以兼容后端代理检查。
      },
    },
  },
})
