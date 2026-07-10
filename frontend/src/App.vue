<script setup>
// 当前根组件只承担应用入口展示；搜索、文献库与图谱功能将在后续阶段拆分为独立页面。
</script>

<template>
  <main class="app-shell">
    <section class="hero" aria-labelledby="app-title">
      <p class="eyebrow">ACADEMIC SEARCH AGENT</p>
      <h1 id="app-title">ScholarFlow</h1>
      <p class="description">面向复杂科研查询的智能论文搜索与推荐系统</p>
      <p class="status">基础工程已就绪，下一步将接入多源论文检索。</p>
    </section>
  </main>
</template>

<style scoped>
.app-shell { /* 为页面提供居中的全屏基础布局。 */
  display: grid; /* 使用网格快速实现水平和垂直居中。 */
  min-height: 100vh; /* 确保页面至少填满浏览器可视区域。 */
  place-items: center; /* 将首屏内容置于页面中央。 */
  padding: 2rem; /* 为窄屏设备保留安全边距。 */
}

.hero { /* 限制首屏内容宽度以保证阅读舒适。 */
  max-width: 42rem; /* 防止描述文本过长影响扫读。 */
}

.eyebrow { /* 标记应用的学术检索定位。 */
  color: #4761b5; /* 使用沉稳蓝色建立信息层级。 */
  font-size: 0.75rem; /* 弱化辅助标签避免抢占标题注意力。 */
  font-weight: 700; /* 保持小字号下的清晰度。 */
  letter-spacing: 0.12em; /* 增加英文标签可读性。 */
}

h1 { /* 突出产品主名称。 */
  margin: 0.5rem 0; /* 控制标题与上下内容的间距。 */
  color: #172554; /* 使用深蓝色传达科研产品的稳定感。 */
  font-size: clamp(2.75rem, 8vw, 5rem); /* 在不同屏幕宽度下保持合适标题大小。 */
}

.description { /* 显示产品核心价值说明。 */
  margin: 0; /* 移除浏览器默认段落间距。 */
  color: #334155; /* 使用中性深灰提升长文本可读性。 */
  font-size: 1.125rem; /* 让核心说明比辅助文字更醒目。 */
}

.status { /* 呈现当前实现阶段，避免将占位页面误解为完整功能。 */
  margin-top: 1.5rem; /* 与产品说明拉开层级间距。 */
  color: #64748b; /* 降低阶段性提示的视觉优先级。 */
}
</style>
