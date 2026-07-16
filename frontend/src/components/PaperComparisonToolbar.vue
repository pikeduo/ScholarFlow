<script setup>
const props = defineProps({ // 只接收页面拥有的选择状态与少量展示文案。
  selectedCount: { type: Number, required: true }, // 当前已选择论文数量。
  maxPapers: { type: Number, default: 5 }, // 展示与后端一致的最大比较数量。
  minPapers: { type: Number, default: 2 }, // 比较按钮启用所需的最小选择数量。
  loading: { type: Boolean, default: false }, // 比较 API 正在读取时禁用操作。
  compareLabel: { type: String, default: '比较已选论文' }, // 允许两页保留各自既有按钮文案。
  loadingLabel: { type: String, default: '正在比较…' }, // 允许页面保留原有加载提示。
  showTitle: { type: Boolean, default: false }, // 搜索页可继续展示“论文比较”标题。
  showClearWhenEmpty: { type: Boolean, default: true }, // 搜索页可保持无选择时隐藏清空按钮。
  disableClearWhenLoading: { type: Boolean, default: true }, // 文献库保留读取中禁止清空，搜索页可允许主动取消在途比较。
})
const emit = defineEmits(['compare', 'clear']) // 工具栏仅通知页面，不直接保存 ID 或访问 API。
</script>

<template>
  <section class="paper-comparison-toolbar" aria-label="论文比较选择">
    <div class="paper-comparison-summary"><strong v-if="showTitle">论文比较</strong><span>{{ `已选择 ${selectedCount} / ${maxPapers} 篇` }}</span></div>
    <div class="paper-comparison-actions"><button type="button" :disabled="loading || selectedCount < minPapers" @click="emit('compare')">{{ loading ? loadingLabel : compareLabel }}</button><button v-if="showClearWhenEmpty || selectedCount" class="paper-comparison-clear" type="button" :disabled="!selectedCount || (loading && disableClearWhenLoading)" @click="emit('clear')">清空</button></div>
  </section>
</template>

<style scoped>
.paper-comparison-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; margin: 1rem 0; padding: 0.85rem 1rem; border: 1px solid #d9e8ef; border-radius: 0.8rem; background: #f5fafc; } /* 提供两个页面共用且紧凑的比较操作区。 */
.paper-comparison-summary { display: flex; align-items: center; gap: 0.65rem; color: #5c7283; font-size: 0.76rem; } /* 并列展示功能名与已选数量。 */
.paper-comparison-summary strong { color: #2e6f95; font-size: 0.8rem; } /* 搜索页标题保持原有视觉层级。 */
.paper-comparison-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; } /* 窄屏下允许两个操作按钮自然换行。 */
button { padding: 0.48rem 0.75rem; border: 0; border-radius: 0.52rem; color: #fff; background: #2e6f95; cursor: pointer; font-size: 0.72rem; font-weight: 800; } /* 保持页面既有主操作样式。 */
button:disabled { cursor: default; opacity: 0.56; } /* 选择不足、加载或无选择时明确禁用。 */
.paper-comparison-clear { color: #416478; background: #e8f1f5; } /* 清空保持次级操作层级。 */
</style>
