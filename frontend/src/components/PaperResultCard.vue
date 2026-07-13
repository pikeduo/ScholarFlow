<script setup>
import { computed, ref, useSlots } from 'vue' // 派生卡片信息、插槽和按需翻译状态。

import { SearchApiError, translatePaperToChinese } from '../services/searchApi.js' // 仅在用户展开摘要后请求后端 DeepSeek 翻译。
import { buildDoiUrl, buildPublicPdfUrl } from '../utils/doi.js' // 将 DOI 和来源明确提供的公开 PDF 链接规范化。

const props = defineProps({ // 声明论文和列表序号输入。
  paper: { type: Object, required: true }, // 接收后端 PaperRecord。
  rank: { type: Number, default: 0 }, // 接收从一开始的结果排名，文献库可隐藏排名列。
  keywords: { type: Array, default: () => [] }, // 接收用户维护或来源提供的关键词。
  queryKeywords: { type: Array, default: () => [] }, // 接收本次 QueryIntent 解析出的检索关键词，仅在搜索结果卡片展示。
  showRank: { type: Boolean, default: true }, // 控制文献库是否展示搜索相关度排名列。
  showScore: { type: Boolean, default: true }, // 控制非搜索场景是否展示相关度分数。
  enableTranslation: { type: Boolean, default: true }, // 控制仅搜索结果可用的字段翻译入口。
  showSearchActions: { type: Boolean, default: true }, // 控制收藏、详情和比较等搜索专属操作。
  saved: { type: Boolean, default: false }, // 标记当前搜索会话中是否已收藏。
  saving: { type: Boolean, default: false }, // 标记收藏请求是否进行中。
  comparisonSelected: { type: Boolean, default: false }, // 标记当前论文是否已加入比较集合。
  comparisonDisabled: { type: Boolean, default: false }, // 标记达到比较上限时是否禁止新增选择。
})
const emit = defineEmits(['save', 'detail', 'compare']) // 将收藏、详情与比较选择操作交给搜索页统一调用 API。
const slots = useSlots() // 检查调用方是否提供卡片尾部自定义操作。
const titleTranslation = ref(null) // 保存当前卡片独立获得的中文标题译文。
const abstractTranslation = ref(null) // 保存当前卡片独立获得的中文摘要译文。
const titleTranslationLoading = ref(false) // 仅标记标题翻译请求，绝不影响摘要按钮状态。
const abstractTranslationLoading = ref(false) // 仅标记摘要翻译请求，绝不影响标题按钮状态。
const titleTranslationError = ref('') // 保存标题翻译的局部公共失败原因。
const abstractTranslationError = ref('') // 保存摘要翻译的局部公共失败原因。
const showTitleTranslation = ref(false) // 控制标题译文是否在原文标题下方显示。
const showAbstractTranslation = ref(false) // 控制摘要译文是否在展开摘要区域显示。

const authors = computed(() => { // 将作者列表压缩为适合卡片的文本。
  const names = (props.paper.authors || []).map((author) => author.name).filter(Boolean) // 提取有效作者名称。
  if (!names.length) return '作者信息暂缺' // 为来源缺失作者提供明确占位。
  return names.length > 4 ? `${names.slice(0, 4).join('、')} 等` : names.join('、') // 长作者列表只展示前四位。
})

const sources = computed(() => { // 优先展示完整多源溯源列表。
  const sourceNames = (props.paper.source_records || []).map((record) => record.source).filter(Boolean) // 提取所有融合来源。
  return [...new Set(sourceNames.length ? sourceNames : [props.paper.source])] // 去重并在无溯源时回退主来源。
})

const doiUrl = computed(() => buildDoiUrl(props.paper.doi)) // 仅为符合 DOI 格式的论文渲染固定 doi.org 链接。
const publicPdfUrl = computed(() => buildPublicPdfUrl(props.paper.open_access_url)) // 仅为来源明确提供的公开 PDF 渲染独立按钮。
const displayQueryKeywords = computed(() => { // 展示本次查询解析提取的主题、方法、任务和数据集关键词。
  const seen = new Set() // 保存大小写无关去重键，避免同一术语在多种解析字段中重复。
  return props.queryKeywords.map((value) => String(value || '').trim()).filter((value) => { const key = value.toLocaleLowerCase(); if (!value || seen.has(key)) return false; seen.add(key); return true }).slice(0, 8) // 保留最多八个查询词，避免遮挡论文来源信息。
})
const displayPaperKeywords = computed(() => { // 优先展示用户维护关键词，并补充来源返回的论文关键词。
  const values = [...props.keywords, ...(props.paper.keywords || [])] // 合并两个关键词来源供文献库和搜索页共用。
  const seen = new Set() // 保存大小写无关去重键。
  return values.map((value) => String(value || '').trim()).filter((value) => { const key = value.toLocaleLowerCase(); if (!value || seen.has(key)) return false; seen.add(key); return true }).slice(0, 12) // 保留最多十二个可扫读关键词。
})
const hasCustomActions = computed(() => Boolean(slots.actions)) // 判断文献库是否注入编辑或删除操作。

const statusMeta = computed(() => { // 将后端三态核验映射为中文展示。
  const mapping = { // 定义稳定状态标签和样式名。
    satisfied: { label: '约束已满足', className: 'is-satisfied' }, // 表示存在公开证据支持。
    uncertain: { label: '需要进一步核验', className: 'is-uncertain' }, // 表示证据不足。
    not_satisfied: { label: '约束未满足', className: 'is-rejected' }, // 为未来展示被排除结果保留映射。
  }
  return mapping[props.paper.constraint_status] || { label: '尚未核验', className: 'is-unknown' } // LLM 降级时显示中性状态。
})

const scoreLabel = computed(() => { // 将归一化 LLM 分数转为百分比。
  const score = props.paper.llm_relevance_score ?? props.paper.cross_encoder_score // LLM 缺失时回退 Cross Encoder 分数。
  return typeof score === 'number' ? `${Math.round(score * 100)}%` : '—' // 缺失分数时不虚构数值。
})

async function ensureTranslation(field) { // 请求后端 SQLite 缓存，未命中时才由后端调用指定字段的翻译服务。
  const loading = field === 'title' ? titleTranslationLoading : abstractTranslationLoading // 将加载状态严格绑定到用户当前操作字段。
  const errorState = field === 'title' ? titleTranslationError : abstractTranslationError // 将错误提示严格绑定到用户当前操作字段。
  const currentTranslation = field === 'title' ? titleTranslation.value : abstractTranslation.value // 读取对应字段的当前卡片内存结果。
  if (currentTranslation || loading.value) return currentTranslation // 已获得或当前字段正在请求时禁止同字段重复调用。
  loading.value = true // 立即反馈当前按钮状态，不锁定另一个字段的翻译操作。
  errorState.value = '' // 清除当前字段用户重试前的旧错误。
  try { // 通过受控后端边界调用 DeepSeek，浏览器不持有任何密钥。
    const translated = await translatePaperToChinese(props.paper.paper_id, field) // 仅传递已保存论文标识和用户选择字段。
    if (field === 'title') titleTranslation.value = translated; else abstractTranslation.value = translated // 只写入当前字段，禁止标题操作影响摘要显示。
    return translated // 返回当前已获得的单字段译文。
  } catch (error) { // 将客户端公共错误映射到当前字段区域。
    errorState.value = error instanceof SearchApiError ? error.message : '论文翻译暂时不可用，请稍后重试' // 不展示网络或服务端内部细节。
    return null // 请求失败时让调用方保持原文并允许重试。
  } finally { // 无论成功失败都恢复用户重试能力。
    loading.value = false // 只结束当前字段的翻译加载状态。
  }
}

async function translateTitle() { // 独立显示标题译文，同时复用统一缓存和模型调用边界。
  if (await ensureTranslation('title')) showTitleTranslation.value = true // 仅在成功获得标题译文后展示中文标题。
}

async function translateAbstract() { // 在用户已展开摘要后独立显示摘要译文。
  if (await ensureTranslation('abstract')) showAbstractTranslation.value = true // 仅在成功获得摘要译文后展示中文摘要。
}
</script>

<template>
  <!-- 单篇论文卡片将身份元数据、核验证据和推荐理由保持在同一阅读单元。 -->
  <article :class="['paper-card', { 'without-rank': !showRank }]">
    <div v-if="showRank" class="rank-column" aria-label="结果排名">
      <span>{{ String(rank).padStart(2, '0') }}</span>
      <template v-if="showScore"><small>相关度</small><strong>{{ scoreLabel }}</strong></template>
    </div>
    <div class="paper-content">
      <div class="paper-badges">
        <span v-for="source in sources" :key="source" class="source-badge">{{ source }}</span>
        <span :class="['status-badge', statusMeta.className]">{{ statusMeta.label }}</span>
        <span v-if="paper.paper_type" class="type-badge">{{ paper.paper_type }}</span>
      </div>
      <h3>
        <a v-if="doiUrl" :href="doiUrl" target="_blank" rel="noopener noreferrer">{{ paper.title }}</a>
        <span v-else>{{ paper.title }}</span>
      </h3>
      <div v-if="enableTranslation" class="title-translation">
        <button type="button" class="translate-button" :disabled="titleTranslationLoading" @click="translateTitle">{{ titleTranslationLoading ? '正在翻译…' : showTitleTranslation ? '已显示中文标题' : '翻译标题' }}</button>
        <p v-if="titleTranslationError" class="translation-error" role="alert">{{ titleTranslationError }}</p>
        <p v-if="showTitleTranslation && titleTranslation" class="translated-title" lang="zh-CN">{{ titleTranslation.text_zh }}</p>
      </div>
      <p class="bibliography">
        <span>{{ authors }}</span>
        <span>{{ paper.year || '年份暂缺' }}</span>
        <span>{{ paper.venue || 'Venue 暂缺' }}</span>
        <span>被引 {{ paper.citation_count || 0 }}</span>
      </p>
      <section v-if="displayQueryKeywords.length || displayPaperKeywords.length" class="keyword-section" aria-label="论文关键词">
        <strong>关键词</strong>
        <div>
          <span v-for="keyword in displayQueryKeywords" :key="`query-${keyword}`" class="query-keyword-badge">检索：{{ keyword }}</span>
          <span v-for="keyword in displayPaperKeywords" :key="`paper-${keyword}`" class="keyword-badge">{{ keyword }}</span>
        </div>
      </section>
      <section v-if="paper.recommendation_reason" class="recommendation" aria-label="推荐理由">
        <span class="recommendation-label">为什么推荐</span>
        <p>{{ paper.recommendation_reason }}</p>
      </section>
      <ul v-if="paper.constraint_evidence?.length" class="evidence-list" aria-label="约束证据">
        <li v-for="evidence in paper.constraint_evidence" :key="evidence">“{{ evidence }}”</li>
      </ul>
      <details v-if="paper.abstract" class="abstract-details">
        <summary>查看摘要</summary>
        <p>{{ paper.abstract }}</p>
        <button v-if="enableTranslation" type="button" class="translate-button" :disabled="abstractTranslationLoading" @click="translateAbstract">{{ abstractTranslationLoading ? '正在翻译…' : showAbstractTranslation ? '已显示中文摘要' : '翻译摘要' }}</button>
        <p v-if="enableTranslation && abstractTranslationError" class="translation-error" role="alert">{{ abstractTranslationError }}</p>
        <section v-if="enableTranslation && showAbstractTranslation && abstractTranslation" class="translated-abstract" lang="zh-CN" aria-label="中文摘要翻译">
          <strong>中文摘要</strong>
          <p>{{ abstractTranslation.text_zh }}</p>
          <small>{{ `由 ${abstractTranslation.model_name} 翻译` }}</small>
        </section>
      </details>
      <div class="paper-footer">
        <div>
          <a v-if="doiUrl" class="doi-link" :href="doiUrl" target="_blank" rel="noopener noreferrer">DOI {{ paper.doi }}</a>
          <span v-else-if="paper.doi">DOI {{ paper.doi }}</span>
          <span v-else-if="paper.arxiv_id">arXiv {{ paper.arxiv_id }}</span>
        </div>
        <div class="paper-actions">
          <slot v-if="hasCustomActions" name="actions" :paper="paper" />
          <template v-else-if="showSearchActions">
            <a v-if="publicPdfUrl" class="public-pdf-link" :href="publicPdfUrl" target="_blank" rel="noopener noreferrer">打开公开 PDF</a>
            <button type="button" :class="{ 'is-selected': comparisonSelected }" :disabled="comparisonDisabled && !comparisonSelected" @click="emit('compare', paper)">{{ comparisonSelected ? '已加入比较' : '加入比较' }}</button>
            <button type="button" class="detail-button" @click="emit('detail', paper)">查看详情</button>
            <button type="button" :class="{ 'is-saved': saved }" :disabled="saving || saved" @click="emit('save', paper)">{{ saving ? '正在收藏…' : saved ? '已收藏' : '收藏到文献库' }}</button>
          </template>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.paper-card { /* 将排名与论文正文组成清晰横向卡片。 */
  display: grid; /* 使用网格固定排名列。 */
  grid-template-columns: 4.5rem minmax(0, 1fr); /* 保持正文可自适应缩放。 */
  overflow: hidden; /* 防止内部背景越过圆角。 */
  border: 1px solid #dfe7ef; /* 用浅边框界定结果。 */
  border-radius: 1.1rem; /* 使用略小于主面板的圆角。 */
  background: #ffffff; /* 确保长文本阅读对比度。 */
  box-shadow: 0 10px 28px rgba(30, 64, 92, 0.045); /* 提供轻微层次。 */
  transition: border-color 160ms ease, transform 160ms ease, box-shadow 160ms ease; /* 平滑响应悬停反馈。 */
}

.paper-card.without-rank { /* 文献库复用卡片时移除搜索专属排名列。 */
  grid-template-columns: minmax(0, 1fr); /* 让正文占满整张收藏卡片。 */
}

.paper-card:hover { /* 强化当前扫读的论文卡片。 */
  border-color: #b8ccdc; /* 使用蓝灰强调边框。 */
  box-shadow: 0 18px 34px rgba(30, 64, 92, 0.09); /* 提升悬停层次。 */
  transform: translateY(-2px); /* 轻微上移而不干扰布局。 */
}

.rank-column { /* 展示排名和相关度。 */
  display: flex; /* 使用纵向弹性布局。 */
  align-items: center; /* 水平居中排名内容。 */
  padding: 1.25rem 0.65rem; /* 保留竖向呼吸。 */
  border-right: 1px solid #e5edf3; /* 分隔排名与正文。 */
  color: #64748b; /* 使用辅助文字色。 */
  background: linear-gradient(180deg, #f4f8fb, #fbfcfd); /* 增加排名列视觉区分。 */
  flex-direction: column; /* 纵向排列编号、标签和分数。 */
}

.rank-column > span { /* 突出列表排名。 */
  color: #9aafc0; /* 保持排名不压过标题。 */
  font-family: Georgia, serif; /* 使用衬线数字。 */
  font-size: 1rem; /* 提供清晰排名定位。 */
}

.rank-column small { /* 标记相关度分数。 */
  margin-top: auto; /* 将分数区域推到底部。 */
  font-size: 0.62rem; /* 保持辅助标签紧凑。 */
}

.rank-column strong { /* 展示百分比相关度。 */
  margin-top: 0.2rem; /* 与标签保持关联。 */
  color: #2e6f95; /* 使用品牌强调色。 */
  font-family: Georgia, serif; /* 使用衬线数字提升辨识。 */
  font-size: 0.9rem; /* 保持分数次于标题。 */
}

.paper-content { /* 包裹论文主要信息。 */
  min-width: 0; /* 允许长标题在网格中正确换行。 */
  padding: 1.25rem 1.35rem; /* 提供卡片阅读留白。 */
}

.paper-badges { /* 横向排列来源与核验状态。 */
  display: flex; /* 使用弹性布局。 */
  flex-wrap: wrap; /* 来源较多时允许换行。 */
  gap: 0.4rem; /* 分隔胶囊标签。 */
  align-items: center; /* 对齐不同标签。 */
}

.source-badge,
.status-badge,
.type-badge,
.keyword-badge,
.query-keyword-badge { /* 统一来源和查询关键词标签基础样式。 */
  padding: 0.22rem 0.48rem; /* 形成紧凑胶囊。 */
  border-radius: 999px; /* 使用完整圆角。 */
  font-size: 0.64rem; /* 控制元数据密度。 */
  font-weight: 700; /* 保证小字号清晰。 */
  letter-spacing: 0.02em; /* 增强英文来源可读性。 */
}

.source-badge { /* 展示论文来源。 */
  color: #24577a; /* 使用蓝色文字。 */
  background: #eaf3f8; /* 使用浅蓝背景。 */
}

.type-badge { /* 展示论文类型。 */
  color: #667085; /* 使用中性文字。 */
  background: #f0f2f5; /* 使用中性背景。 */
}

.keyword-badge { /* 展示用户或来源提供的论文关键词。 */
  color: #6a4d1f; /* 使用暖色与来源和核验状态区分。 */
  background: #fff3dc; /* 以低饱和背景保持关键词可扫读。 */
}

.query-keyword-badge { /* 展示 Query Agent 解析出的本次检索关键词。 */
  color: #356d55; /* 使用绿色与论文自身关键词区分。 */
  background: #e9f7ee; /* 以浅绿背景提示该词来自本次检索而非来源元数据。 */
}

.keyword-section { /* 将关键词放在出版信息下方，避免与来源和核验状态混杂。 */
  display: grid; /* 以紧凑双层结构组织标题和关键词胶囊。 */
  gap: 0.35rem; /* 保持出版信息与关键词之间的清晰层次。 */
  margin-top: 0.75rem; /* 与书目信息分隔但仍属于同一论文元数据区域。 */
}

.keyword-section > strong { /* 标记关键词区域语义，避免用户误认来源标签。 */
  color: #6b7f92; /* 使用辅助色弱化区域标题。 */
  font-size: 0.66rem; /* 保持比论文标题更低的视觉权重。 */
}

.keyword-section > div { /* 容纳可换行的检索关键词和论文自身关键词。 */
  display: flex; /* 横向排列关键词胶囊。 */
  flex-wrap: wrap; /* 关键词较多时保持卡片宽度稳定。 */
  gap: 0.35rem; /* 分隔相邻关键词。 */
}

.status-badge.is-satisfied { /* 标记证据支持的约束满足。 */
  color: #28745a; /* 使用可信绿色。 */
  background: #e8f7f0; /* 使用浅绿背景。 */
}

.status-badge.is-uncertain { /* 标记证据不足。 */
  color: #8a5a18; /* 使用琥珀文字。 */
  background: #fff4d8; /* 使用浅琥珀背景。 */
}

.status-badge.is-rejected,
.status-badge.is-unknown { /* 标记未满足或未执行核验。 */
  color: #667085; /* 使用中性色避免过度警示。 */
  background: #f0f2f5; /* 使用中性背景。 */
}

h3 { /* 设置论文标题。 */
  margin: 0.75rem 0 0.45rem; /* 与标签和书目信息分隔。 */
  color: #17324d; /* 使用深蓝保证阅读。 */
  font-family: Georgia, "Noto Serif SC", serif; /* 强化论文标题的出版感。 */
  font-size: clamp(1.05rem, 2vw, 1.28rem); /* 在不同屏幕保持合适字号。 */
  line-height: 1.4; /* 允许长标题舒适换行。 */
}

h3 a { /* 设置可访问论文标题链接。 */
  color: inherit; /* 保持标题配色。 */
  text-decoration-color: #a8c3d4; /* 使用柔和下划线。 */
  text-decoration-thickness: 1px; /* 避免下划线过重。 */
  text-underline-offset: 0.2em; /* 提升中英文标题可读性。 */
}

.title-translation { /* 组织标题下方独立的翻译操作与译文。 */
  display: flex; /* 让未显示译文时按钮保持紧凑布局。 */
  flex-wrap: wrap; /* 长中文标题出现时允许自然换行。 */
  align-items: baseline; /* 保持按钮和译文视觉对齐。 */
  gap: 0.55rem; /* 分隔翻译操作与中文标题。 */
  margin: -0.1rem 0 0.55rem; /* 紧跟原文标题并与书目信息分隔。 */
}

.translated-title { /* 展示用户主动请求的中文标题译文。 */
  margin: 0; /* 由标题翻译容器统一控制与相邻元素的间距。 */
  color: #2e6f95; /* 使用品牌色区分原文与译文。 */
  font-size: 0.82rem; /* 保持译文为标题的辅助信息。 */
  font-weight: 700; /* 提升中文标题的扫读辨识度。 */
  line-height: 1.6; /* 保证长中文标题舒适换行。 */
}

.bibliography { /* 横向展示作者、年份、venue 和引用数。 */
  display: flex; /* 使用弹性布局。 */
  flex-wrap: wrap; /* 窄屏允许元数据换行。 */
  gap: 0.35rem 0; /* 通过伪元素统一横向分隔。 */
  margin: 0; /* 清除默认段落间距。 */
  color: #6b7f92; /* 使用次级文字色。 */
  font-size: 0.75rem; /* 保持书目信息紧凑。 */
  line-height: 1.6; /* 多行时保持可读。 */
}

.bibliography span:not(:last-child)::after { /* 在书目信息之间添加分隔点。 */
  margin: 0 0.5rem; /* 保持分隔点两侧空间。 */
  color: #becbd6; /* 弱化分隔符。 */
  content: "·"; /* 使用常见书目分隔符。 */
}

.recommendation { /* 突出证据支撑的推荐理由。 */
  display: grid; /* 纵向排列标签和理由。 */
  gap: 0.35rem; /* 分隔标签与正文。 */
  margin-top: 0.9rem; /* 与书目信息拉开层级。 */
  padding: 0.8rem 0.9rem; /* 提供理由背景留白。 */
  border-left: 3px solid #5d9ab4; /* 用品牌色标记推荐信息。 */
  border-radius: 0 0.65rem 0.65rem 0; /* 保持左侧证据线清晰。 */
  background: #f3f8fb; /* 使用浅蓝背景。 */
}

.recommendation-label { /* 标记推荐理由类型。 */
  color: #2e6f95; /* 使用品牌强调色。 */
  font-size: 0.65rem; /* 作为辅助标题。 */
  font-weight: 800; /* 保证标签辨识度。 */
  letter-spacing: 0.08em; /* 建立标签感。 */
}

.recommendation p { /* 设置推荐理由正文。 */
  margin: 0; /* 清除默认段落间距。 */
  color: #334e68; /* 使用高可读正文色。 */
  font-size: 0.82rem; /* 与摘要建立层级。 */
  line-height: 1.65; /* 提升中文长句可读性。 */
}

.evidence-list { /* 展示可回溯的原文证据。 */
  display: flex; /* 横向排列短证据片段。 */
  flex-wrap: wrap; /* 证据较长时自动换行。 */
  gap: 0.35rem; /* 分隔多个证据。 */
  margin: 0.7rem 0 0; /* 与推荐理由保持关联。 */
  padding: 0; /* 移除列表默认缩进。 */
  list-style: none; /* 使用胶囊而非列表圆点。 */
}

.evidence-list li { /* 设置单条证据片段。 */
  max-width: 100%; /* 防止长证据溢出卡片。 */
  padding: 0.28rem 0.5rem; /* 形成轻量证据胶囊。 */
  overflow: hidden; /* 隐藏超长单行内容。 */
  border: 1px solid #d8e5ec; /* 提供证据边界。 */
  border-radius: 0.45rem; /* 使用小圆角区别状态标签。 */
  color: #587184; /* 使用次级正文色。 */
  background: #fbfdfe; /* 保持证据低对比背景。 */
  font-size: 0.7rem; /* 控制证据信息密度。 */
  text-overflow: ellipsis; /* 超长证据显示省略号。 */
  white-space: nowrap; /* 保持胶囊单行。 */
}

.abstract-details { /* 提供按需展开的摘要。 */
  margin-top: 0.8rem; /* 与证据或书目信息分隔。 */
  color: #64748b; /* 使用次级文字色。 */
  font-size: 0.76rem; /* 摘要作为补充阅读信息。 */
}

.abstract-details summary { /* 设置摘要展开控件。 */
  width: fit-content; /* 仅让文字区域可点击。 */
  cursor: pointer; /* 告知用户可展开。 */
  color: #2e6f95; /* 使用品牌交互色。 */
  font-weight: 700; /* 提升操作可见性。 */
}

.abstract-details p { /* 设置展开后的摘要正文。 */
  margin: 0.6rem 0 0; /* 与摘要操作分隔。 */
  color: #52697d; /* 使用舒适正文色。 */
  line-height: 1.75; /* 提升长摘要阅读体验。 */
}

.translate-button { /* 提供仅在摘要展开后出现的按需翻译入口。 */
  margin-top: 0.7rem; /* 与原文摘要区分开来。 */
  padding: 0.42rem 0.68rem; /* 保持与卡片其他次级按钮一致的点击面积。 */
  border: 1px solid #b8ccdc; /* 使用低强调蓝灰边界。 */
  border-radius: 0.5rem; /* 延续卡片圆角语言。 */
  color: #2e6f95; /* 使用品牌交互色。 */
  background: #f3f8fb; /* 表达该操作不会默认触发模型调用。 */
  cursor: pointer; /* 明确用户可主动请求翻译。 */
  font-size: 0.7rem; /* 保持摘要区域操作紧凑。 */
  font-weight: 800; /* 小字号下保持操作可见。 */
}

.translate-button:disabled { /* 翻译完成或请求期间防止重复模型调用。 */
  cursor: default; /* 禁止重复交互的视觉反馈。 */
  opacity: 0.7; /* 降低已完成或加载状态的强调度。 */
}

.translation-error { /* 展示不泄露内部细节的翻译失败提示。 */
  color: #a44c45; /* 使用克制红色标记当前摘要操作失败。 */
  font-size: 0.72rem; /* 保持错误提示属于局部辅助信息。 */
}

.translated-abstract { /* 将中文摘要译文与原文明确分层。 */
  margin-top: 0.75rem; /* 与翻译按钮和原文摘要拉开距离。 */
  padding: 0.75rem 0.85rem; /* 为长中文译文提供稳定阅读留白。 */
  border-left: 3px solid #7eafc4; /* 使用细色条标记机器翻译内容。 */
  border-radius: 0 0.55rem 0.55rem 0; /* 保持与推荐理由区一致的视觉语言。 */
  background: #f5fafc; /* 使用浅色背景避免与原文混淆。 */
}

.translated-abstract strong { /* 标记中文摘要译文标题。 */
  color: #2e6f95; /* 使用品牌色强调译文标签。 */
  font-size: 0.72rem; /* 保持辅助标题层级。 */
}

.translated-abstract p { /* 设置中文摘要译文正文。 */
  margin: 0.4rem 0 0; /* 与译文标签保持紧凑关联。 */
  color: #405b6d; /* 保持长中文内容的舒适可读性。 */
  line-height: 1.8; /* 提升中文段落阅读体验。 */
}

.translated-abstract small { /* 说明译文由实际模型生成。 */
  display: block; /* 让模型说明独占一行避免打断译文。 */
  margin-top: 0.45rem; /* 与译文正文建立清晰间距。 */
  color: #8295a4; /* 使用弱化文字避免喧宾夺主。 */
  font-size: 0.64rem; /* 保持来源说明为最低视觉层级。 */
}

.paper-footer { /* 展示 DOI 或 arXiv 等必要身份信息。 */
  display: flex; /* 横向排列身份标识。 */
  align-items: center; /* 对齐身份标识与收藏操作。 */
  justify-content: space-between; /* 将收藏操作置于卡片右侧。 */
  gap: 1rem; /* 分隔标识与操作。 */
  margin-top: 0.9rem; /* 与正文内容分隔。 */
  color: #95a4b2; /* 降低技术标识权重。 */
  font-size: 0.62rem; /* 控制长标识占用空间。 */
}

.paper-footer > div:first-child { /* 组合论文必要身份标识。 */
  display: flex; /* 横向排列技术标识。 */
  flex-wrap: wrap; /* 窄屏允许标识换行。 */
  gap: 0.75rem; /* 分隔不同标识。 */
  min-width: 0; /* 允许长 DOI 在卡片中收缩。 */
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; /* 使用等宽字体方便辨认 ID。 */
}

.doi-link { /* 将已校验 DOI 显示为可在新标签页解析的来源链接。 */
  color: #527e98; /* 使用可读蓝色提示可点击而不过度抢占论文标题。 */
  text-decoration-color: #b7cfdd; /* 使用低强调下划线保持技术标识清晰。 */
  text-underline-offset: 0.18em; /* 提升小字号 DOI 链接的可读性。 */
}

.paper-actions { /* 组合详情读取与收藏操作，避免卡片底部按钮分散。 */
  display: flex; /* 横向排列两个紧凑操作。 */
  flex: 0 0 auto; /* 防止操作区被长 DOI 压缩。 */
  flex-wrap: wrap; /* 窄屏允许按钮换行。 */
  justify-content: flex-end; /* 让操作与右侧边缘对齐。 */
  gap: 0.45rem; /* 分隔详情和收藏按钮。 */
}

.paper-footer button { /* 设置结果卡详情与收藏操作。 */
  flex: 0 0 auto; /* 防止按钮被长 DOI 压缩。 */
  padding: 0.45rem 0.7rem; /* 提供紧凑点击区域。 */
  border: 1px solid #b8ccdc; /* 使用品牌蓝灰边界。 */
  border-radius: 0.55rem; /* 与卡片小控件协调。 */
  color: #2e6f95; /* 使用品牌交互色。 */
  background: #f3f8fb; /* 使用浅蓝背景。 */
  cursor: pointer; /* 告知用户可收藏。 */
  font-size: 0.68rem; /* 保持操作紧凑。 */
  font-weight: 800; /* 提升可发现性。 */
}

.public-pdf-link { /* 提供不改变 DOI 主入口的独立公开 PDF 访问按钮。 */
  flex: 0 0 auto; /* 防止操作区压缩 PDF 按钮。 */
  padding: 0.45rem 0.7rem; /* 与相邻操作保持一致的点击面积。 */
  border: 1px solid #b9dacb; /* 使用绿色边界区分公开全文入口。 */
  border-radius: 0.55rem; /* 与结果卡其他操作保持一致。 */
  color: #28745a; /* 使用可信绿色表达来源公开访问。 */
  background: #e8f7f0; /* 以浅绿背景突出合法公开 PDF。 */
  font-size: 0.68rem; /* 保持操作区紧凑。 */
  font-weight: 800; /* 提升小字号可发现性。 */
  text-decoration: none; /* 使用按钮视觉而非默认链接下划线。 */
}

.paper-footer button.is-saved { /* 标记已收藏论文。 */
  border-color: #b9dacb; /* 使用可信绿色边界。 */
  color: #28745a; /* 使用绿色文字。 */
  background: #e8f7f0; /* 使用浅绿背景。 */
}

.paper-footer button.is-selected { /* 标记已加入当前小集合比较。 */
  border-color: #b7d0bc; /* 使用绿色边框区别比较选择状态。 */
  color: #28745a; /* 使用可信绿色文字。 */
  background: #e8f7f0; /* 使用浅绿背景提示可再次取消。 */
}

.paper-footer button.detail-button { /* 将只读详情入口保持为次级操作。 */
  color: #536f7f; /* 使用低饱和文字区别收藏主操作。 */
  background: #ffffff; /* 保持轻量白底。 */
}

.paper-footer button:disabled { /* 表达请求中或已完成状态。 */
  cursor: default; /* 禁止重复收藏操作。 */
  opacity: 0.78; /* 弱化禁用按钮。 */
}

@media (max-width: 560px) { /* 调整手机论文卡布局。 */
  .paper-card { /* 将排名压缩为更窄列。 */
    grid-template-columns: 3.3rem minmax(0, 1fr); /* 为正文保留更多空间。 */
  }

  .paper-content { /* 缩小手机卡片内边距。 */
    padding: 1rem; /* 保持内容舒适且不拥挤。 */
  }

  .paper-footer { /* 手机端纵向排列标识和收藏按钮。 */
    align-items: flex-start; /* 左对齐两组内容。 */
    flex-direction: column; /* 避免长 DOI 挤压按钮。 */
  }
}
</style>
