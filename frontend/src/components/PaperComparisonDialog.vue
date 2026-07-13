<script setup>
defineProps({ // 声明由搜索页或文献库页面提供的事实型比较状态。
  result: { type: Object, default: null }, // 接收后端按用户顺序返回的固定列结果。
  loading: { type: Boolean, default: false }, // 接收比较快照读取中的加载状态。
  error: { type: String, default: '' }, // 接收不泄露内部实现的公共比较错误。
})
const emit = defineEmits(['close']) // 将关闭动作交还给拥有比较选择状态的页面。
const fields = [ // 固定可审阅的论文事实字段，避免由前端推断研究结论。
  { label: '出版信息', key: 'publication' }, // 展示作者、年份和 venue 等书目信息。
  { label: '关键词', key: 'keywords' }, // 展示论文来源或规范化关键词。
  { label: '摘要', key: 'abstract' }, // 展示已保存摘要，不读取 PDF 全文。
  { label: '推荐理由', key: 'recommendation_reason' }, // 展示已有 LLM 核验结果。
  { label: '约束状态', key: 'constraint_status' }, // 展示已有约束核验状态。
  { label: '核验证据', key: 'constraint_evidence' }, // 展示已有证据而非生成新解释。
  { label: '来源', key: 'sources' }, // 展示多源事实溯源信息。
]

function formatValue(value) { // 将数组与空字段转换为适合固定列展示的文本。
  if (Array.isArray(value)) return value.join(' · ') || '暂无' // 保持数组事实的原始顺序并提供空值占位。
  return value || '暂无' // 标量空值不显示技术空字符串。
}
</script>

<template>
  <div v-if="result || loading || error" class="comparison-backdrop" @click.self="emit('close')">
    <aside class="comparison-panel" role="dialog" aria-modal="true" aria-labelledby="paper-comparison-title">
      <button class="comparison-close" type="button" aria-label="关闭论文比较" @click="emit('close')">×</button>
      <p class="eyebrow">SAVED PAPER COMPARISON</p>
      <h2 id="paper-comparison-title">论文事实对比</h2>
      <p v-if="loading" class="comparison-status">正在读取已保存论文与核验证据…</p>
      <p v-else-if="error" class="comparison-error" role="alert">{{ error }}</p>
      <div v-else-if="result" class="comparison-grid">
        <div class="comparison-row comparison-head" :style="{ '--comparison-count': result.items.length }"><strong>字段</strong><strong v-for="item in result.items" :key="item.paper_id">{{ item.title }}</strong></div>
        <div v-for="field in fields" :key="field.key" class="comparison-row" :style="{ '--comparison-count': result.items.length }"><strong>{{ field.label }}</strong><p v-for="item in result.items" :key="item.paper_id">{{ formatValue(item[field.key]) }}</p></div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.comparison-backdrop { position: fixed; z-index: 31; inset: 0; display: grid; place-items: center; padding: 1rem; background: rgba(18, 43, 60, 0.34); } /* 以居中遮罩承载可跨页面复用的比较结果。 */
.comparison-panel { position: relative; width: min(76rem, 100%); max-height: min(48rem, 100%); overflow: auto; padding: 2.1rem; border-radius: 1rem; background: #fff; box-shadow: 0 18px 42px rgba(15, 40, 57, 0.22); } /* 保证多列长摘要比较仍可滚动阅读。 */
.comparison-close { position: absolute; top: 0.9rem; right: 1rem; width: 2rem; height: 2rem; border: 1px solid #cbd9e3; border-radius: 50%; color: #486579; background: #f7fafc; cursor: pointer; font-size: 1.25rem; line-height: 1; } /* 提供稳定的关闭入口。 */
.eyebrow { margin: 0 0 0.55rem; color: #2e6f95; font-size: 0.66rem; font-weight: 800; letter-spacing: 0.17em; } /* 保持两个一级页面一致的英文眉题。 */
h2 { margin: 0; padding-right: 2.5rem; color: #18354f; font-family: Georgia, "Noto Serif SC", serif; font-size: clamp(1.35rem, 3vw, 2rem); } /* 突出事实型比较的标题。 */
.comparison-status, .comparison-error { margin: 0.85rem 0 0; color: #64788a; font-size: 0.8rem; } /* 统一加载和错误状态的辅助层级。 */
.comparison-error { padding: 0.75rem; border-radius: 0.65rem; color: #9b3c36; background: #fff0ee; } /* 使用安全且可扫读的错误样式。 */
.comparison-grid { display: grid; gap: 0.55rem; margin-top: 1.2rem; } /* 纵向排列标题行与事实字段行。 */
.comparison-row { display: grid; grid-template-columns: 9rem repeat(var(--comparison-count), minmax(12rem, 1fr)); overflow: hidden; border: 1px solid #dce7ed; border-radius: 0.7rem; } /* 按用户选择数量生成稳定的固定列。 */
.comparison-row > strong, .comparison-row > p { min-width: 0; margin: 0; padding: 0.7rem; border-right: 1px solid #e4edf2; color: #52697d; font-size: 0.74rem; line-height: 1.65; overflow-wrap: anywhere; } /* 让长 DOI、摘要和证据在单元格中自然折行。 */
.comparison-row > strong { color: #31566e; background: #f4f8fb; font-weight: 800; } /* 为字段列提供稳定视觉锚点。 */
.comparison-row > :last-child { border-right: 0; } /* 移除最末列冗余分隔线。 */
.comparison-head > strong:not(:first-child) { color: #18354f; font-family: Georgia, "Noto Serif SC", serif; font-size: 0.82rem; } /* 突出每篇被比较论文的标题。 */
@media (max-width: 760px) { .comparison-panel { padding: 1.35rem; } .comparison-row { min-width: 42rem; } } /* 手机保留横向滚动，避免压缩事实列到不可读。 */
</style>
