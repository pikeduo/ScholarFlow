<script setup>
import { onMounted, reactive, ref } from 'vue' // 管理筛选、编辑草稿和请求状态。

import { deleteLibraryItem, LibraryApiError, listLibraryItems, normalizeTags, searchLibraryItemsSemantically, updateLibraryItem } from '../services/libraryApi.js' // 调用个人文献库稳定 API。

const items = ref([]) // 保存当前筛选条件下的收藏记录。
const total = ref(0) // 保存后端返回的筛选结果总数。
const loading = ref(false) // 标记列表请求状态。
const semanticLoading = ref(false) // 标记自然语言语义检索请求状态。
const busyItemId = ref('') // 标记正在更新或删除的单条记录。
const message = ref({ text: '', tone: 'success' }) // 保存页面级安全操作反馈。
const filters = reactive({ tag: '', readingStatus: '' }) // 保存标签和阅读状态筛选条件。
const semanticQuery = ref('') // 保存用户输入的文献库自然语言检索文本。
const semanticMode = ref(false) // 标记当前列表是否来自语义检索而非普通筛选。
const semanticScores = reactive({}) // 按收藏 ID 保存后端返回的语义相似度。
const drafts = reactive({}) // 按 item_id 保存标签、备注和阅读状态编辑草稿。

const statusLabels = { unread: '未读', reading: '阅读中', read: '已读' } // 将后端稳定枚举映射为中文。

onMounted(loadItems) // 页面首次打开时加载全部收藏。

function createDraft(item) { // 从后端记录构造独立可编辑草稿。
  drafts[item.item_id] = { tags: (item.tags || []).join(', '), note: item.note || '', readingStatus: item.reading_status } // 避免直接修改响应对象。
}

async function loadItems() { // 使用当前筛选条件刷新文献库列表。
  if (loading.value || semanticLoading.value) return // 防止与自然语言检索并发覆盖当前结果。
  loading.value = true // 展示加载状态并禁用筛选操作。
  semanticMode.value = false // 普通筛选结果不展示旧语义相似度。
  clearSemanticScores() // 清理上一次语义结果的分数字典。
  message.value = { text: '', tone: 'success' } // 清除旧反馈。
  try { // 捕获 API 客户端已净化错误。
    const result = await listLibraryItems(filters) // 提交标签和阅读状态筛选。
    items.value = result.items // 替换当前列表。
    total.value = result.total // 更新筛选结果数量。
    for (const item of result.items) createDraft(item) // 为每条记录建立编辑草稿。
  } catch (error) { // 隔离网络和响应契约错误。
    message.value = { text: error instanceof LibraryApiError ? error.message : '加载文献库时出现未知错误，请稍后重试', tone: 'error' } // 展示安全错误。
  } finally { // 恢复筛选操作。
    loading.value = false // 清除加载状态。
  }
}

function resetFilters() { // 清除筛选并重新加载全部收藏。
  filters.tag = '' // 清除标签筛选。
  filters.readingStatus = '' // 清除阅读状态筛选。
  loadItems() // 刷新完整列表。
}

async function searchSemantically() { // 在当前标签和阅读状态范围内执行自然语言检索。
  if (loading.value || semanticLoading.value) return // 防止与普通列表请求并发覆盖当前结果。
  semanticLoading.value = true // 展示检索状态并禁用筛选与语义按钮。
  message.value = { text: '', tone: 'success' } // 清除旧页面反馈。
  try { // 捕获 API 客户端已净化错误。
    const result = await searchLibraryItemsSemantically(semanticQuery.value, filters) // 提交自然语言查询和当前结构化筛选。
    items.value = result.items.map((entry) => entry.item) // 将语义响应转换为既有卡片可复用的收藏对象列表。
    total.value = result.total // 更新当前语义结果数量。
    semanticMode.value = true // 标记卡片应展示相似度分数。
    clearSemanticScores() // 先清理旧查询残留分数。
    for (const entry of result.items) semanticScores[entry.item.item_id] = entry.semantic_score // 按收藏 ID 保存对应相似度。
    for (const item of items.value) createDraft(item) // 保留语义结果中的标签、状态和备注编辑能力。
    if (result.degraded) message.value = { text: result.degradation_reason || '语义索引暂不可用，已使用论文元数据词项匹配', tone: 'warning' } // 明确提示用户当前不是向量语义结果。
  } catch (error) { // 处理网络、输入或响应契约故障。
    message.value = { text: error instanceof LibraryApiError ? error.message : '检索文献库时出现未知错误，请稍后重试', tone: 'error' } // 展示安全错误。
  } finally { // 恢复页面交互。
    semanticLoading.value = false // 清除自然语言检索状态。
  }
}

function clearSemanticSearch() { // 清除自然语言检索文本并恢复普通筛选列表。
  semanticQuery.value = '' // 清空用户当前语义查询。
  semanticMode.value = false // 先隐藏相似度展示。
  clearSemanticScores() // 清理旧查询分数。
  loadItems() // 按当前标签和状态恢复普通列表。
}

function clearSemanticScores() { // 删除响应式分数字典中的所有旧收藏条目。
  for (const itemId of Object.keys(semanticScores)) delete semanticScores[itemId] // 避免上一轮分数泄漏到普通筛选卡片。
}

function formatSemanticScore(itemId) { // 将零到一分数转换为用户易读百分比。
  const score = semanticScores[itemId] // 读取对应收藏的语义相似度。
  return typeof score === 'number' ? `${Math.round(score * 100)}%` : '—' // 防御部分响应或编辑后缺失分数。
}

async function saveChanges(item) { // 提交单条收藏的用户属性修改。
  if (busyItemId.value) return // 防止并发修改或删除。
  const draft = drafts[item.item_id] // 读取对应编辑草稿。
  busyItemId.value = item.item_id // 禁用当前列表操作。
  message.value = { text: '', tone: 'success' } // 清除旧反馈。
  try { // 捕获 API 公共错误。
    const updated = await updateLibraryItem(item.item_id, { tags: normalizeTags(draft.tags), note: draft.note, readingStatus: draft.readingStatus }) // 提交完整用户属性。
    const index = items.value.findIndex((candidate) => candidate.item_id === item.item_id) // 定位当前列表记录。
    if (index >= 0) items.value[index] = updated // 使用后端最终状态替换本地记录。
    createDraft(updated) // 同步规范化后的标签和状态。
    message.value = { text: '文献库记录已更新', tone: 'success' } // 提示保存成功。
  } catch (error) { // 处理不存在、断网或服务故障。
    message.value = { text: error instanceof LibraryApiError ? error.message : '更新文献库记录时出现未知错误', tone: 'error' } // 展示安全错误。
  } finally { // 恢复列表操作。
    busyItemId.value = '' // 清除忙碌状态。
  }
}

async function removeItem(item) { // 删除用户明确选择的收藏记录。
  if (busyItemId.value) return // 防止并发操作。
  if (globalThis.confirm && !globalThis.confirm(`确定从文献库删除《${item.paper.title}》吗？`)) return // 使用浏览器确认防止误删收藏。
  busyItemId.value = item.item_id // 禁用当前列表操作。
  message.value = { text: '', tone: 'success' } // 清除旧反馈。
  try { // 捕获 API 公共错误。
    await deleteLibraryItem(item.item_id) // 请求后端原子删除记录。
    items.value = items.value.filter((candidate) => candidate.item_id !== item.item_id) // 从当前列表移除已删除记录。
    total.value = Math.max(0, total.value - 1) // 同步当前筛选结果数量。
    delete semanticScores[item.item_id] // 同步移除已删除收藏的语义分数。
    delete drafts[item.item_id] // 清理不再使用的编辑草稿。
    message.value = { text: '收藏已从文献库删除', tone: 'success' } // 提示删除完成。
  } catch (error) { // 处理记录不存在或服务故障。
    message.value = { text: error instanceof LibraryApiError ? error.message : '删除文献库记录时出现未知错误', tone: 'error' } // 展示安全错误。
  } finally { // 恢复列表操作。
    busyItemId.value = '' // 清除忙碌状态。
  }
}
</script>

<template>
  <div class="library-page">
    <section class="library-hero" aria-labelledby="library-title">
      <p class="eyebrow">PERSONAL RESEARCH LIBRARY</p>
      <h1 id="library-title">我的文献库</h1>
      <p>沉淀搜索结果，维护标签、阅读状态与个人备注。</p>
      <form class="filter-panel" aria-label="文献库筛选" @submit.prevent="loadItems">
        <label>标签<input v-model="filters.tag" type="text" placeholder="例如：重点、时间序列" :disabled="loading || semanticLoading"></label>
        <label>阅读状态<select v-model="filters.readingStatus" :disabled="loading || semanticLoading"><option value="">全部状态</option><option value="unread">未读</option><option value="reading">阅读中</option><option value="read">已读</option></select></label>
        <button type="submit" :disabled="loading || semanticLoading">{{ loading ? '正在加载…' : '应用筛选' }}</button>
        <button class="secondary" type="button" :disabled="loading || semanticLoading" @click="resetFilters">清除筛选</button>
      </form>
      <form class="semantic-panel" aria-label="文献库自然语言检索" @submit.prevent="searchSemantically">
        <label>自然语言检索<input v-model="semanticQuery" type="search" placeholder="例如：使用语义检索的多语言论文" :disabled="loading || semanticLoading"></label>
        <button type="submit" :disabled="loading || semanticLoading">{{ semanticLoading ? '正在检索…' : '语义检索' }}</button>
        <button class="secondary" type="button" :disabled="loading || semanticLoading || (!semanticMode && !semanticQuery)" @click="clearSemanticSearch">恢复列表</button>
      </form>
    </section>

    <main class="library-content">
      <header class="content-header"><div><p class="eyebrow">{{ semanticMode ? 'SEMANTIC LIBRARY RESULTS' : 'SAVED PAPERS' }}</p><h2>{{ semanticMode ? '语义检索结果' : '收藏论文' }} <span>{{ total }}</span></h2></div></header>
      <p v-if="message.text" :class="['page-message', `is-${message.tone}`]" role="status">{{ message.text }}</p>
      <div v-if="loading || semanticLoading" class="loading-card">{{ semanticLoading ? '正在理解查询并检索文献库…' : '正在读取个人文献库…' }}</div>
      <div v-else-if="!items.length" class="empty-library"><strong>文献库中暂无匹配论文</strong><p>可以回到“文献搜索”，将感兴趣的结果收藏到这里。</p></div>
      <div v-else class="library-list">
        <article v-for="item in items" :key="item.item_id" class="library-card">
          <div class="paper-summary">
            <div class="summary-badges"><span>{{ item.paper.source }}</span><span>{{ statusLabels[item.reading_status] }}</span><span v-if="item.paper.year">{{ item.paper.year }}</span><span v-if="semanticMode" class="semantic-score">语义相似度 {{ formatSemanticScore(item.item_id) }}</span></div>
            <h3>{{ item.paper.title }}</h3>
            <p>{{ (item.paper.authors || []).map((author) => author.name).slice(0, 4).join('、') || '作者信息暂缺' }} · {{ item.paper.venue || 'Venue 暂缺' }}</p>
            <details v-if="item.paper.abstract"><summary>查看摘要</summary><p>{{ item.paper.abstract }}</p></details>
          </div>
          <form v-if="drafts[item.item_id]" class="item-editor" @submit.prevent="saveChanges(item)">
            <label>标签<input v-model="drafts[item.item_id].tags" type="text" placeholder="使用逗号分隔" :disabled="Boolean(busyItemId)"></label>
            <label>阅读状态<select v-model="drafts[item.item_id].readingStatus" :disabled="Boolean(busyItemId)"><option value="unread">未读</option><option value="reading">阅读中</option><option value="read">已读</option></select></label>
            <label class="note-field">个人备注<textarea v-model="drafts[item.item_id].note" rows="3" maxlength="5000" placeholder="记录阅读重点、疑问或后续行动" :disabled="Boolean(busyItemId)"></textarea></label>
            <div class="item-actions"><button type="submit" :disabled="Boolean(busyItemId)">{{ busyItemId === item.item_id ? '正在保存…' : '保存修改' }}</button><button class="delete-button" type="button" :disabled="Boolean(busyItemId)" @click="removeItem(item)">删除收藏</button></div>
          </form>
        </article>
      </div>
    </main>
  </div>
</template>

<style scoped>
.library-page { min-height: calc(100vh - 4.5rem); padding-bottom: 5rem; background: radial-gradient(circle at 90% 0%, rgba(126, 178, 193, 0.16), transparent 28rem), #f6f9fa; } /* 建立与搜索页一致的研究工作台背景。 */
.library-hero, .library-content { width: min(1120px, calc(100% - 2rem)); margin: 0 auto; } /* 对齐两个一级页面内容宽度。 */
.library-hero { padding: clamp(3rem, 7vw, 5.5rem) 0 2rem; } /* 为文献库标题和筛选器提供首屏留白。 */
.eyebrow { margin: 0 0 0.55rem; color: #2e6f95; font-size: 0.66rem; font-weight: 800; letter-spacing: 0.17em; } /* 使用品牌英文眉题。 */
h1 { margin: 0; color: #17324d; font-family: Georgia, "Noto Serif SC", serif; font-size: clamp(2.2rem, 5vw, 4rem); font-weight: 500; } /* 突出个人知识资产入口。 */
.library-hero > p:not(.eyebrow) { margin: 0.8rem 0 0; color: #687f91; } /* 说明文献库核心用途。 */
.filter-panel { display: grid; grid-template-columns: minmax(12rem, 1fr) 13rem auto auto; gap: 0.75rem; align-items: end; margin-top: 1.6rem; padding: 1rem; border: 1px solid #d8e4eb; border-radius: 1rem 1rem 0 0; background: rgba(255, 255, 255, 0.9); } /* 横向组织基础筛选能力。 */
.semantic-panel { display: grid; grid-template-columns: minmax(12rem, 1fr) auto auto; gap: 0.75rem; align-items: end; padding: 0 1rem 1rem; border: 1px solid #d8e4eb; border-top: 0; border-radius: 0 0 1rem 1rem; background: rgba(245, 250, 252, 0.9); } /* 在同一结构化筛选范围内提供自然语言入口。 */
label { display: grid; gap: 0.35rem; color: #536b7f; font-size: 0.68rem; font-weight: 800; } /* 统一编辑字段标签。 */
input, select, textarea { width: 100%; padding: 0.65rem 0.7rem; border: 1px solid #cedce5; border-radius: 0.58rem; color: #29465d; background: #fbfdfe; font: inherit; font-size: 0.72rem; } /* 保持筛选与编辑控件一致。 */
textarea { resize: vertical; line-height: 1.55; } /* 允许按备注长度调整高度。 */
button { padding: 0.65rem 0.85rem; border: 0; border-radius: 0.58rem; color: #fff; background: #2e6f95; cursor: pointer; font: inherit; font-size: 0.7rem; font-weight: 800; } /* 设置默认主操作样式。 */
button.secondary { border: 1px solid #cad9e3; color: #5c7486; background: #fff; } /* 弱化清除筛选操作。 */
button:disabled { cursor: wait; opacity: 0.6; } /* 表达进行中的异步操作。 */
.library-content { display: grid; gap: 1rem; } /* 纵向组织标题、反馈和收藏列表。 */
.content-header h2 { margin: 0; color: #18354f; font-family: Georgia, "Noto Serif SC", serif; font-size: 1.8rem; font-weight: 500; } /* 展示当前收藏结果数量。 */
.content-header h2 span { color: #2e6f95; } /* 使用品牌色突出数量。 */
.page-message { margin: 0; padding: 0.7rem 0.8rem; border-radius: 0.65rem; font-size: 0.72rem; } /* 展示更新和删除反馈。 */
.page-message.is-success { color: #28745a; background: #e8f7f0; } /* 标记成功操作。 */
.page-message.is-error { color: #9b3c36; background: #fff0ee; } /* 标记请求失败。 */
.page-message.is-warning { color: #8a5b17; background: #fff8de; } /* 标记语义模型或索引不可用时的词项匹配降级。 */
.loading-card, .empty-library { padding: 3rem 1rem; border: 1px dashed #c9d8e2; border-radius: 1rem; color: #718496; text-align: center; background: rgba(255, 255, 255, 0.72); } /* 提供加载和空集合状态。 */
.empty-library strong { color: #334e68; } /* 突出空状态主说明。 */
.empty-library p { margin: 0.45rem 0 0; font-size: 0.75rem; } /* 提供返回搜索页的操作提示。 */
.library-list { display: grid; gap: 0.9rem; } /* 纵向排列收藏卡片。 */
.library-card { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(18rem, 0.75fr); gap: 1.2rem; padding: 1.2rem; border: 1px solid #dfe7ef; border-radius: 1rem; background: #fff; box-shadow: 0 10px 28px rgba(30, 64, 92, 0.045); } /* 同时展示论文信息和用户属性。 */
.paper-summary { min-width: 0; } /* 允许长论文标题正确换行。 */
.summary-badges { display: flex; flex-wrap: wrap; gap: 0.35rem; } /* 排列来源、状态和年份标签。 */
.summary-badges span { padding: 0.22rem 0.45rem; border-radius: 999px; color: #456d84; background: #eaf3f8; font-size: 0.62rem; font-weight: 700; } /* 使用低密度元数据胶囊。 */
.summary-badges .semantic-score { color: #5d4a8f; background: #eee9fb; } /* 使用独立色调突出本轮自然语言检索相似度。 */
h3 { margin: 0.7rem 0 0.35rem; color: #17324d; font-family: Georgia, "Noto Serif SC", serif; font-size: 1.12rem; line-height: 1.45; } /* 突出收藏论文标题。 */
.paper-summary > p { margin: 0; color: #718496; font-size: 0.72rem; } /* 展示作者和 venue。 */
details { margin-top: 0.8rem; color: #607487; font-size: 0.72rem; } /* 按需显示论文摘要。 */
summary { width: fit-content; color: #2e6f95; cursor: pointer; font-weight: 700; } /* 标记摘要展开操作。 */
details p { line-height: 1.65; } /* 提升摘要长文本可读性。 */
.item-editor { display: grid; grid-template-columns: minmax(0, 1fr) 8rem; gap: 0.65rem; padding-left: 1.2rem; border-left: 1px solid #e2e9ee; } /* 组织标签、状态和备注编辑。 */
.note-field, .item-actions { grid-column: 1 / -1; } /* 让备注与操作横跨编辑区。 */
.item-actions { display: flex; justify-content: flex-end; gap: 0.5rem; } /* 将保存和删除操作靠右排列。 */
.delete-button { border: 1px solid #e2c7c4; color: #9b4b45; background: #fff5f4; } /* 使用克制危险色标记删除操作。 */
@media (max-width: 820px) { .filter-panel { grid-template-columns: 1fr 1fr; } .semantic-panel { grid-template-columns: 1fr 1fr; } .library-card { grid-template-columns: 1fr; } .item-editor { padding-top: 1rem; padding-left: 0; border-top: 1px solid #e2e9ee; border-left: 0; } } /* 平板将筛选器和卡片编辑区收敛为两列或纵向布局。 */
@media (max-width: 560px) { .filter-panel, .semantic-panel, .item-editor { grid-template-columns: 1fr; } .note-field, .item-actions { grid-column: auto; } .item-actions { align-items: stretch; flex-direction: column; } } /* 手机使用单列控件和全宽操作。 */
</style>
