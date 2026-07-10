import { defineConfig } from 'vite' // 导入 Vite 配置辅助函数。
import vue from '@vitejs/plugin-vue' // 导入 Vue 单文件组件编译插件。

export default defineConfig({ // 导出前端构建工具配置。
  plugins: [vue()], // 启用 Vue 3 单文件组件支持。
  server: { // 配置本地开发服务器。
    port: 5173, // 固定默认端口以便前后端联调。
    strictPort: true, // 端口被占用时明确报错，避免误连其他应用。
  },
})
