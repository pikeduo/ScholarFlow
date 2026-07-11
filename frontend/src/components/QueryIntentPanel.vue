<script setup>
import { reactive, ref, watch } from 'vue' // 管理可编辑意图副本和面板错误。

import { SearchApiError, splitTerms, validateQueryIntent } from '../services/searchApi.js' // 复用稳定词项和契约校验。

const props = defineProps({ // 声明查询意图、规划统计和请求状态。
  intent: { type: Object, required: true }, // 接收后端真实执行的 QueryIntent。
  planningMeta: { type: Object, default: () => ({}) }, // 接收 Query Agent 模型、Token 和耗时统计。
  disabled: { type: Boolean, default: false }, // 请求期间禁用重复提交。
})
const emit = defineEmits(['resubmit']) // 向页面提交校验后的完整 QueryIntent。

const fields = reactive({}) // 保存适合表单编辑的字符串字段。
const panelError = ref('') // 保存不泄露内部信息的本地校验错误。
const expanded = ref(true) // 默认展示解析结果，便于用户核对 Query Agent。

watch(() => props.intent, loadIntent, { immediate: true, deep: true }) // 新检索完成后同步替换编辑副本。

function joinTerms(value) { // 将后端数组转换为可编辑逗号文本。
  return Array.isArray(value) ? value.join(', ') : '' // 空值稳定显示为空文本。
}

function loadIntent(intent) { // 从最新响应重建表单，避免残留上一轮编辑。
  const yearRange = Array.isArray(intent?.year_range) ? intent.year_range : [] // 读取可选年份闭区间。
  Object.assign(fields, { // 只保存面板允许编辑的字段和计数。
    normalizedQuery: intent?.normalized_query || '', // 展示实际用于学术 API 的英文检索式。
    researchTopics: joinTerms(intent?.research_topics), // 展示研究主题。
    methods: joinTerms(intent?.methods), // 展示方法。
    tasks: joinTerms(intent?.tasks), // 展示任务。
    datasets: joinTerms(intent?.datasets), // 展示数据集。
    mustInclude: joinTerms(intent?.must_include), // 展示硬约束。
    shouldInclude: joinTerms(intent?.should_include), // 展示软偏好。
    exclude: joinTerms(intent?.exclude), // 展示排除条件。
    domains: joinTerms(intent?.domains), // 展示来源路由领域。
    paperTypes: joinTerms(intent?.paper_types), // 展示论文类型。
    startYear: yearRange[0] || '', // 展示可选起始年份。
    endYear: yearRange[1] || '', // 展示可选结束年份。
    sourceRecallCount: intent?.source_recall_count || intent?.target_paper_count || 20, // 展示每来源召回上限。
    targetPaperCount: intent?.target_paper_count || 20, // 展示最终结果上限。
    subqueries: Array.isArray(intent?.subqueries) ? intent.subqueries.map((item) => item.query).join('\n') : '', // 每行展示一条英文子查询。
  })
  panelError.value = '' // 清除旧意图对应的错误。
}

function submitEditedIntent() { // 将表单重建为完整 QueryIntent 并请求直接重搜。
  panelError.value = '' // 清除上次校验错误。
  try { // 将校验错误留在当前面板展示。
    const startYear = fields.startYear === '' ? null : Number(fields.startYear) // 转换可选起始年份。
    const endYear = fields.endYear === '' ? null : Number(fields.endYear) // 转换可选结束年份。
    if ((startYear === null) !== (endYear === null)) throw new SearchApiError('年份范围需要同时填写起始和结束年份') // 拒绝单边年份。
    const queries = String(fields.subqueries || '').split('\n').map((query) => query.trim()).filter(Boolean).slice(0, 3) // 限制最多三条有效子查询。
    const previousSubqueries = Array.isArray(props.intent.subqueries) ? props.intent.subqueries : [] // 保留原子查询目的信息。
    const editedIntent = { // 在原完整契约上覆盖允许编辑的字段。
      ...props.intent, // 保留语言、作者、机构、搜索模式等未展示字段。
      normalized_query: fields.normalizedQuery, // 更新英文主检索式。
      research_topics: splitTerms(fields.researchTopics), // 更新研究主题。
      methods: splitTerms(fields.methods), // 更新方法。
      tasks: splitTerms(fields.tasks), // 更新任务。
      datasets: splitTerms(fields.datasets), // 更新数据集。
      must_include: splitTerms(fields.mustInclude), // 更新硬约束。
      should_include: splitTerms(fields.shouldInclude), // 更新偏好。
      exclude: splitTerms(fields.exclude), // 更新排除条件。
      domains: splitTerms(fields.domains), // 更新领域路由依据。
      paper_types: splitTerms(fields.paperTypes), // 更新论文类型。
      year_range: startYear === null ? null : [startYear, endYear], // 更新完整年份闭区间。
      source_recall_count: Number(fields.sourceRecallCount), // 更新来源召回规模。
      target_paper_count: Number(fields.targetPaperCount), // 更新最终结果规模。
      subqueries: queries.map((query, index) => ({ query, language: 'en', purpose: previousSubqueries[index]?.purpose || 'method' })), // 保留既有目的并为新增项提供稳定默认值。
    }
    emit('resubmit', validateQueryIntent(editedIntent)) // 提交独立且已校验的完整计划。
  } catch (error) { // 捕获表单和契约错误。
    panelError.value = error instanceof SearchApiError ? error.message : '查询解析结果无法提交，请检查编辑内容' // 展示安全说明。
  }
}
</script>

<template>
  <section class="intent-panel" aria-labelledby="intent-title">
    <header>
      <div>
        <p>QUERY INTENT</p>
        <h2 id="intent-title">查询解析</h2>
        <span>可核对并修改实际检索条件；重新搜索将跳过 Query Agent。</span>
      </div>
      <button type="button" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起' : '展开编辑' }}</button>
    </header>
    <div class="planning-stats" aria-label="查询规划统计">
      <span>模型 {{ planningMeta.modelName || '直接意图' }}</span>
      <span>Token {{ (planningMeta.promptTokens || 0) + (planningMeta.completionTokens || 0) }}</span>
      <span>耗时 {{ planningMeta.durationMs ? `${planningMeta.durationMs} ms` : '—' }}</span>
      <span>召回 {{ fields.sourceRecallCount }} → 最终 {{ fields.targetPaperCount }}</span>
    </div>
    <form v-if="expanded" @submit.prevent="submitEditedIntent">
      <label class="wide">英文规范查询<input v-model="fields.normalizedQuery" type="text" :disabled="disabled" required></label>
      <label>研究主题<input v-model="fields.researchTopics" type="text" :disabled="disabled" placeholder="逗号分隔"></label>
      <label>方法<input v-model="fields.methods" type="text" :disabled="disabled" placeholder="逗号分隔"></label>
      <label>任务<input v-model="fields.tasks" type="text" :disabled="disabled" placeholder="逗号分隔"></label>
      <label>数据集<input v-model="fields.datasets" type="text" :disabled="disabled" placeholder="逗号分隔"></label>
      <label>必须包含<input v-model="fields.mustInclude" type="text" :disabled="disabled" placeholder="逗号分隔"></label>
      <label>优先包含<input v-model="fields.shouldInclude" type="text" :disabled="disabled" placeholder="逗号分隔"></label>
      <label>排除<input v-model="fields.exclude" type="text" :disabled="disabled" placeholder="逗号分隔"></label>
      <label>领域<input v-model="fields.domains" type="text" :disabled="disabled" placeholder="逗号分隔"></label>
      <label>论文类型<input v-model="fields.paperTypes" type="text" :disabled="disabled" placeholder="article, conference"></label>
      <div class="year-fields"><label>起始年份<input v-model="fields.startYear" type="number" min="1800" max="2100" :disabled="disabled"></label><label>结束年份<input v-model="fields.endYear" type="number" min="1800" max="2100" :disabled="disabled"></label></div>
      <div class="count-fields"><label>每来源召回<input v-model="fields.sourceRecallCount" type="number" min="1" max="100" :disabled="disabled"></label><label>最终结果<input v-model="fields.targetPaperCount" type="number" min="1" max="20" :disabled="disabled"></label></div>
      <label class="wide">英文子查询（每行一条，最多三条）<textarea v-model="fields.subqueries" rows="3" :disabled="disabled"></textarea></label>
      <p v-if="panelError" class="panel-error" role="alert">{{ panelError }}</p>
      <button class="resubmit-button" type="submit" :disabled="disabled">使用修改后的条件重新搜索</button>
    </form>
  </section>
</template>

<style scoped>
.intent-panel { margin-top: 1rem; padding: 1.2rem; border: 1px solid #d6e1e8; border-radius: 1rem; background: rgba(255, 255, 255, 0.9); } /* 将解析区与检索轨迹组织为独立卡片。 */
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; } /* 横向排列标题和折叠操作。 */
header p { margin: 0; color: #2e6f95; font-size: 0.62rem; font-weight: 800; letter-spacing: 0.16em; } /* 延续搜索页英文眉题风格。 */
header h2 { margin: 0.25rem 0; color: #203f57; font-size: 1.05rem; } /* 突出查询解析区名称。 */
header span { color: #718496; font-size: 0.7rem; } /* 使用次级文字解释重搜行为。 */
header button { border: 0; color: #2e6f95; background: transparent; cursor: pointer; font-weight: 700; } /* 提供轻量折叠操作。 */
.planning-stats { display: flex; flex-wrap: wrap; gap: 0.45rem; margin-top: 0.9rem; } /* 紧凑排列模型和成本统计。 */
.planning-stats span { padding: 0.3rem 0.55rem; border-radius: 999px; color: #587083; background: #edf3f6; font-size: 0.65rem; } /* 使用胶囊区分观测字段。 */
form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.75rem; margin-top: 1rem; } /* 桌面端使用两列控制面板高度。 */
label { display: grid; gap: 0.35rem; color: #415c70; font-size: 0.68rem; font-weight: 700; } /* 纵向组织字段名和输入框。 */
input, textarea { box-sizing: border-box; width: 100%; padding: 0.62rem 0.7rem; border: 1px solid #cddbe4; border-radius: 0.55rem; color: #203f57; background: #fbfdfe; font: inherit; font-weight: 400; } /* 保持所有意图字段一致的输入反馈。 */
textarea { resize: vertical; } /* 允许按子查询数量调整文本区高度。 */
.wide, .panel-error, .resubmit-button { grid-column: 1 / -1; } /* 让主查询、错误和操作横跨两列。 */
.year-fields, .count-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.5rem; } /* 成对展示范围与候选数量。 */
.panel-error { margin: 0; color: #a44c45; font-size: 0.7rem; } /* 使用克制红色展示本地校验错误。 */
.resubmit-button { justify-self: end; padding: 0.68rem 1rem; border: 0; border-radius: 0.6rem; color: #fff; background: #2e6f95; cursor: pointer; font-weight: 800; } /* 强调跳过 Query Agent 的重搜操作。 */
.resubmit-button:disabled { cursor: wait; opacity: 0.55; } /* 请求期间明确禁用重复提交。 */
@media (max-width: 700px) { form { grid-template-columns: 1fr; } .wide, .panel-error, .resubmit-button { grid-column: auto; } } /* 窄屏将编辑字段切换为单列。 */
</style>
