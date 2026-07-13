<script setup>
import { ref } from 'vue' // 管理两个首版一级页面的轻量切换状态。

import LibraryPage from './pages/LibraryPage.vue' // 引入个人文献库基础页面。
import SearchPage from './pages/SearchPage.vue' // 引入当前首版唯一可用的文献搜索页面。

const activePage = ref('search') // 默认进入文献搜索模块。

function showPage(pageName) { // 切换一级页面并将视口返回内容顶部。
  activePage.value = pageName // 更新当前页面。
  globalThis.scrollTo?.({ top: 0, behavior: 'smooth' }) // 避免从长结果列表中部进入文献库。
}
</script>

<template>
  <!-- 根组件只负责全局应用框架，页面业务保持在独立组件中。 -->
  <div class="app-frame">
    <header class="topbar">
      <a class="brand" href="#main-content" aria-label="ScholarFlow 文献搜索首页" @click.prevent="showPage('search')">
        <span class="brand-mark" aria-hidden="true">研</span>
        <span class="brand-copy">
          <strong>ScholarFlow</strong>
          <small>研索</small>
        </span>
      </a>
      <nav class="primary-nav" aria-label="主要功能">
        <button :class="['nav-link', { 'is-active': activePage === 'search' }]" type="button" :aria-current="activePage === 'search' ? 'page' : undefined" @click="showPage('search')">文献搜索</button>
        <button :class="['nav-link', { 'is-active': activePage === 'library' }]" type="button" :aria-current="activePage === 'library' ? 'page' : undefined" @click="showPage('library')">我的文献库</button>
      </nav>
      <span class="system-status"><i aria-hidden="true"></i> 多源检索链路就绪</span>
    </header>
    <main id="main-content">
      <SearchPage v-if="activePage === 'search'" />
      <LibraryPage v-else />
    </main>
  </div>
</template>

<style scoped>
.app-frame { /* 建立固定顶栏与可滚动内容区。 */
  min-height: 100vh; /* 保证短内容时背景仍覆盖完整视口。 */
}

.topbar { /* 提供产品标识、一级导航和系统状态。 */
  position: sticky; /* 滚动长结果列表时保持导航可用。 */
  top: 0; /* 将顶栏固定在视口顶部。 */
  z-index: 20; /* 避免结果卡片覆盖顶栏。 */
  display: flex; /* 横向排列品牌、导航和状态。 */
  min-height: 4.5rem; /* 保留舒适点击区域。 */
  align-items: center; /* 垂直居中顶栏内容。 */
  gap: 2.5rem; /* 拉开品牌与导航层级。 */
  padding: 0.75rem clamp(1rem, 4vw, 4rem); /* 在宽窄屏之间平滑调整边距。 */
  border-bottom: 1px solid rgba(148, 163, 184, 0.22); /* 用轻边框分隔页面内容。 */
  background: rgba(248, 250, 252, 0.92); /* 保持滚动时内容隐约可见。 */
  backdrop-filter: blur(16px); /* 提升半透明顶栏的阅读清晰度。 */
}

.brand { /* 将品牌标识作为可访问的跳转链接。 */
  display: inline-flex; /* 横向组合图形和双语名称。 */
  align-items: center; /* 对齐品牌图形与文字。 */
  gap: 0.75rem; /* 控制标识与名称间距。 */
  color: inherit; /* 继承全局深色文字。 */
  text-decoration: none; /* 移除链接默认下划线。 */
}

.brand-mark { /* 使用文字图章建立简单且无需图片的品牌识别。 */
  display: grid; /* 居中单个中文字符。 */
  width: 2.5rem; /* 固定图章宽度。 */
  height: 2.5rem; /* 固定图章高度。 */
  place-items: center; /* 将“研”字完全居中。 */
  border-radius: 0.8rem; /* 使用柔和圆角匹配产品视觉。 */
  color: #ffffff; /* 提升图章文字对比度。 */
  background: linear-gradient(145deg, #173f7a, #2e6f95); /* 以深海蓝突出科研定位。 */
  box-shadow: 0 8px 18px rgba(23, 63, 122, 0.2); /* 增加轻微浮层感。 */
  font-family: "STKaiti", "KaiTi", serif; /* 使用书卷感字体强化中文品牌。 */
  font-size: 1.2rem; /* 保证图章内字符清晰。 */
}

.brand-copy { /* 纵向排列英文品牌与中文名。 */
  display: grid; /* 使用网格形成紧凑双行。 */
  line-height: 1.05; /* 减少双行之间空隙。 */
}

.brand-copy strong { /* 突出英文产品名。 */
  color: #102a43; /* 使用高对比海军蓝。 */
  font-family: Georgia, "Times New Roman", serif; /* 使用学术出版风格衬线字体。 */
  font-size: 1.05rem; /* 控制品牌不挤占导航空间。 */
}

.brand-copy small { /* 标注中文产品名。 */
  margin-top: 0.25rem; /* 与英文名称建立清晰间距。 */
  color: #718096; /* 降低辅助名称权重。 */
  font-size: 0.72rem; /* 保持双语层级。 */
  letter-spacing: 0.22em; /* 增加中文短词的视觉呼吸。 */
}

.primary-nav { /* 承载两个首版一级模块。 */
  display: flex; /* 横向排列导航项。 */
  align-self: stretch; /* 让活动态底线贴近顶栏底部。 */
  align-items: center; /* 垂直居中导航文字。 */
  gap: 0.35rem; /* 保持导航项之间紧凑关联。 */
}

.nav-link { /* 统一两个可用一级导航的基础样式。 */
  position: relative; /* 为活动态底线提供定位上下文。 */
  display: inline-flex; /* 对齐标题和即将开放标签。 */
  height: 100%; /* 扩大导航点击区域。 */
  align-items: center; /* 垂直居中文字。 */
  gap: 0.45rem; /* 分隔导航标题和辅助标签。 */
  padding: 0 1rem; /* 提供足够横向点击空间。 */
  border: 0; /* 移除按钮默认边界。 */
  color: #64748b; /* 默认使用次级文字色。 */
  background: transparent; /* 让活动底线成为唯一状态装饰。 */
  cursor: pointer; /* 告知用户两个模块均可进入。 */
  font-family: inherit; /* 避免按钮使用系统默认字体。 */
  font-size: 0.9rem; /* 保持顶栏导航紧凑。 */
  font-weight: 600; /* 提升导航辨识度。 */
  text-decoration: none; /* 移除链接默认下划线。 */
}

.nav-link.is-active { /* 标记当前一级模块。 */
  color: #173f7a; /* 使用品牌主色突出活动状态。 */
}

.nav-link.is-active::after { /* 绘制活动模块底线。 */
  position: absolute; /* 相对导航项定位到底部。 */
  right: 1rem; /* 与文字右边界对齐。 */
  bottom: -0.75rem; /* 贴合顶栏下边缘。 */
  left: 1rem; /* 与文字左边界对齐。 */
  height: 2px; /* 使用细线保持克制。 */
  border-radius: 999px; /* 让底线端点圆润。 */
  background: #2e6f95; /* 使用品牌强调色。 */
  content: ""; /* 创建纯装饰伪元素。 */
}

.system-status { /* 展示后端功能状态。 */
  display: inline-flex; /* 横向对齐状态点与文字。 */
  align-items: center; /* 垂直居中状态点。 */
  gap: 0.45rem; /* 分隔状态点和说明。 */
  margin-left: auto; /* 将状态推至顶栏右侧。 */
  color: #52716b; /* 使用低饱和绿色表达可用。 */
  font-size: 0.78rem; /* 将状态保持为辅助信息。 */
}

.system-status i { /* 绘制服务状态点。 */
  width: 0.45rem; /* 固定圆点尺寸。 */
  height: 0.45rem; /* 保持圆形比例。 */
  border-radius: 50%; /* 将方块变为圆点。 */
  background: #38a169; /* 使用绿色表达已连接能力。 */
  box-shadow: 0 0 0 4px rgba(56, 161, 105, 0.12); /* 增加可辨识的柔和外圈。 */
}

@media (max-width: 720px) { /* 针对手机收敛顶栏信息密度。 */
  .topbar { /* 调整窄屏顶栏。 */
    gap: 0.75rem; /* 缩小品牌与导航间距。 */
  }

  .brand-copy,
  .system-status { /* 隐藏窄屏非核心信息。 */
    display: none; /* 为查询和结果内容保留空间。 */
  }

  .primary-nav { /* 将导航推到右侧。 */
    margin-left: auto; /* 使用剩余空间分隔品牌标识。 */
  }

  .nav-link { /* 缩小窄屏导航内边距。 */
    padding: 0 0.55rem; /* 避免导航发生换行。 */
  }
}
</style>
