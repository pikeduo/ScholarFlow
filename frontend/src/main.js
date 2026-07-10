import { createApp } from 'vue' // 导入 Vue 应用创建函数。

import App from './App.vue' // 导入根界面组件。
import './styles/base.css' // 导入全局基础样式。

createApp(App).mount('#app') // 将 Vue 应用挂载至入口页面容器。
