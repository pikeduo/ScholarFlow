<script setup>
import { computed, ref, watch } from 'vue' // 管理详情字段和两种独立翻译状态。

import { SearchApiError, translatePaperToChinese } from '../services/searchApi.js' // 通过统一后端边界读取字段级缓存译文。
import { buildDoiUrl, buildPublicPdfUrl } from '../utils/doi.js' // 仅展示经过校验的 DOI 与公开 PDF 入口。

const props = defineProps({ // 声明由搜索页或文献库页面提供的详情状态。
  paper: { type: Object, default: null }, // 接收已从本地 SQLite 快照读取的论文详情。
  loading: { type: Boolean, default: false }, // 接收详情读取中的加载状态。
  error: { type: String, default: '' }, // 接收不泄露内部实现的公共读取错误。
})
const emit = defineEmits(['close']) // 将关闭操作交还给拥有详情状态的页面。
const titleTranslation = ref(null) // 保存当前详情论文的标题中文译文。
const abstractTranslation = ref(null) // 保存当前详情论文的摘要中文译文。
const titleLoading = ref(false) // 仅标记标题翻译请求，不能影响摘要按钮。
const abstractLoading = ref(false) // 仅标记摘要翻译请求，不能影响标题按钮。
const titleError = ref('') // 保存标题翻译的局部公共错误。
const abstractError = ref('') // 保存摘要翻译的局部公共错误。
let translationVersion = 0 // 用于丢弃切换论文后迟到的翻译响应。

const doiUrl = computed(() => buildDoiUrl(props.paper?.doi)) // 只为详情中的合法 DOI 生成固定新标签页链接。
const publicPdfUrl = computed(() => buildPublicPdfUrl(props.paper?.open_access_url)) // 只为来源明确给出的公开 PDF 生成入口。

watch(() => props.paper?.paper_id, resetTranslations) // 切换详情论文时清空上一条论文的字段级译文。

function resetTranslations() { // 清空两种字段翻译状态并让旧异步响应失效。
  translationVersion += 1 // 递增版本号以识别切换前发起的翻译请求。
  titleTranslation.value = null // 禁止新论文显示旧中文标题。
  abstractTranslation.value = null // 禁止新论文显示旧中文摘要。
  titleLoading.value = false // 防止关闭或切换后遗留标题加载状态。
  abstractLoading.value = false // 防止关闭或切换后遗留摘要加载状态。
  titleError.value = '' // 清除标题字段旧错误。
  abstractError.value = '' // 清除摘要字段旧错误。
}

async function translateField(field) { // 独立翻译用户点击的标题或摘要字段。
  if (!props.paper) return // 详情尚未读取完成时不得构造翻译请求。
  const paperId = props.paper.paper_id // 固定当前论文标识以防详情切换。
  const version = translationVersion // 保存当前详情版本以识别过期响应。
  const translation = field === 'title' ? titleTranslation : abstractTranslation // 选择对应字段的译文状态。
  const loading = field === 'title' ? titleLoading : abstractLoading // 选择对应字段的加载状态。
  const error = field === 'title' ? titleError : abstractError // 选择对应字段的错误状态。
  if (translation.value || loading.value) return // 已命中缓存或请求中时禁止同字段重复调用。
  loading.value = true // 只让当前字段按钮进入加载状态。
  error.value = '' // 清除用户重试前的局部错误。
  try { // 调用同一个字段级翻译接口，后端优先读取 SQLite 缓存。
    const translated = await translatePaperToChinese(paperId, field) // 浏览器只提交论文标识和允许字段。
    if (version !== translationVersion || props.paper?.paper_id !== paperId) return // 切换详情后丢弃不再属于当前论文的响应。
    translation.value = translated // 只写入当前字段的成功译文。
  } catch (requestError) { // 将已净化客户端错误显示在当前字段附近。
    if (version !== translationVersion || props.paper?.paper_id !== paperId) return // 切换详情后不显示旧请求错误。
    error.value = requestError instanceof SearchApiError ? requestError.message : '论文翻译暂时不可用，请稍后重试' // 不暴露网络或服务端内部细节。
  } finally { // 无论成功或失败都恢复当前字段的重试能力。
    if (version === translationVersion && props.paper?.paper_id === paperId) loading.value = false // 仅结束当前详情论文和字段的加载状态。
  }
}
</script>

<template>
  <div v-if="paper || loading || error" class="paper-detail-backdrop" @click.self="emit('close')">
    <aside class="paper-detail-panel" role="dialog" aria-modal="true" aria-labelledby="paper-detail-title">
      <button class="detail-close" type="button" aria-label="关闭论文详情" @click="emit('close')">×</button>
      <p class="eyebrow">SAVED PAPER DETAIL</p>
      <p v-if="loading" class="detail-status">正在读取已保存的论文详情…</p>
      <p v-else-if="error" class="detail-error" role="alert">{{ error }}</p>
      <template v-else-if="paper">
        <h2 id="paper-detail-title">{{ paper.title }}</h2>
        <div class="detail-title-translation"><button type="button" class="detail-translate-button" :disabled="titleLoading" @click="translateField('title')">{{ titleLoading ? '正在翻译…' : titleTranslation ? '已显示中文标题' : '翻译标题' }}</button><p v-if="titleError" class="detail-translation-error" role="alert">{{ titleError }}</p><p v-if="titleTranslation" class="detail-translated-title" lang="zh-CN">{{ titleTranslation.text_zh }}</p></div>
        <p class="detail-meta">{{ `${(paper.authors || []).map((author) => author.name).filter(Boolean).join('、') || '作者信息暂缺'} · ${paper.year || '年份暂缺'} · ${paper.venue || 'Venue 暂缺'}` }}</p>
        <dl class="detail-identifiers"><div><dt>来源</dt><dd>{{ paper.source }}</dd></div><div v-if="paper.doi"><dt>DOI</dt><dd><a v-if="doiUrl" :href="doiUrl" target="_blank" rel="noopener noreferrer">{{ paper.doi }}</a><span v-else>{{ paper.doi }}</span></dd></div><div v-if="paper.arxiv_id"><dt>arXiv</dt><dd>{{ paper.arxiv_id }}</dd></div><div v-if="paper.openalex_id"><dt>OpenAlex</dt><dd>{{ paper.openalex_id }}</dd></div><div v-if="paper.semantic_scholar_id"><dt>Semantic Scholar</dt><dd>{{ paper.semantic_scholar_id }}</dd></div></dl>
        <section v-if="paper.abstract" class="detail-section"><h3>摘要</h3><p>{{ paper.abstract }}</p><button type="button" class="detail-translate-button" :disabled="abstractLoading" @click="translateField('abstract')">{{ abstractLoading ? '正在翻译…' : abstractTranslation ? '已显示中文摘要' : '翻译摘要' }}</button><p v-if="abstractError" class="detail-translation-error" role="alert">{{ abstractError }}</p><section v-if="abstractTranslation" class="detail-translated-abstract" lang="zh-CN" aria-label="中文摘要翻译"><strong>中文摘要</strong><p>{{ abstractTranslation.text_zh }}</p><small>{{ `由 ${abstractTranslation.model_name} 翻译` }}</small></section></section>
        <section v-if="paper.keywords?.length" class="detail-section"><h3>关键词</h3><p>{{ paper.keywords.join(' · ') }}</p></section>
        <section v-if="paper.constraint_evidence?.length" class="detail-section"><h3>约束证据</h3><ul><li v-for="evidence in paper.constraint_evidence" :key="evidence">{{ evidence }}</li></ul></section>
        <a v-if="doiUrl" class="detail-link" :href="doiUrl" target="_blank" rel="noopener noreferrer">打开 DOI 页面</a><a v-if="publicPdfUrl" class="detail-pdf-link" :href="publicPdfUrl" target="_blank" rel="noopener noreferrer">打开公开 PDF</a>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.paper-detail-backdrop { position: fixed; z-index: 30; inset: 0; display: grid; justify-items: end; background: rgba(18, 43, 60, 0.34); } /* 使用全屏遮罩承载可由两页复用的详情抽屉。 */
.paper-detail-panel { position: relative; width: min(42rem, 100%); height: 100%; overflow-y: auto; padding: 2.1rem; background: #fff; box-shadow: -18px 0 38px rgba(15, 40, 57, 0.16); } /* 保持长摘要可滚动的高对比阅读面板。 */
.detail-close { position: absolute; top: 0.9rem; right: 1rem; width: 2rem; height: 2rem; border: 1px solid #cbd9e3; border-radius: 50%; color: #486579; background: #f7fafc; cursor: pointer; font-size: 1.25rem; line-height: 1; } /* 提供始终可见的关闭入口。 */
.eyebrow { margin: 0 0 0.55rem; color: #2e6f95; font-size: 0.66rem; font-weight: 800; letter-spacing: 0.17em; } /* 保持搜索页与文献库一致的详情眉题。 */
h2 { margin: 0; padding-right: 2.5rem; color: #18354f; font-family: Georgia, "Noto Serif SC", serif; font-size: clamp(1.35rem, 3vw, 2rem); line-height: 1.35; } /* 突出论文原文标题。 */
.detail-title-translation { display: grid; gap: 0.45rem; margin-top: 0.65rem; } /* 让标题翻译紧跟原文并保持独立状态。 */
.detail-translate-button { width: fit-content; padding: 0.42rem 0.68rem; border: 1px solid #b8ccdc; border-radius: 0.5rem; color: #2e6f95; background: #f3f8fb; cursor: pointer; font-size: 0.72rem; font-weight: 800; } /* 为两种字段提供一致的按需翻译入口。 */
.detail-translate-button:disabled { cursor: default; opacity: 0.72; } /* 翻译中只禁用当前字段按钮。 */
.detail-translation-error { margin: 0; color: #9b3c36; font-size: 0.72rem; line-height: 1.55; } /* 在对应字段附近显示安全错误。 */
.detail-translated-title { margin: 0; color: #2e6f95; font-size: 0.95rem; font-weight: 700; line-height: 1.65; } /* 展示不附加前缀的中文标题。 */
.detail-meta, .detail-status, .detail-error { margin: 0.75rem 0 0; color: #64788a; font-size: 0.78rem; line-height: 1.6; } /* 统一详情辅助信息与状态排版。 */
.detail-error { padding: 0.75rem; border-radius: 0.65rem; color: #9b3c36; background: #fff0ee; } /* 使用可扫读的安全错误样式。 */
.detail-identifiers { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 0.7rem; margin: 1.2rem 0; } /* 自适应排列论文身份标识。 */
.detail-identifiers div { padding: 0.65rem; border-radius: 0.65rem; background: #f4f8fb; } /* 为每个标识提供独立阅读背景。 */
.detail-identifiers dt { color: #6f8799; font-size: 0.63rem; font-weight: 800; } /* 弱化标识类型标签。 */
.detail-identifiers dd { margin: 0.25rem 0 0; overflow-wrap: anywhere; color: #38556a; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 0.7rem; } /* 保证长 DOI 与平台标识不会溢出。 */
.detail-identifiers dd a { color: #2e6f95; text-decoration-color: #a8c3d4; text-underline-offset: 0.18em; } /* 仅将已验证 DOI 作为可点击入口。 */
.detail-section { margin-top: 1.2rem; } /* 分隔摘要、关键词和约束证据等事实区域。 */
.detail-section h3 { margin: 0; color: #31566e; font-size: 0.82rem; } /* 保持详情子标题的层级。 */
.detail-section p, .detail-section ul { margin: 0.45rem 0 0; color: #52697d; font-size: 0.78rem; line-height: 1.75; } /* 保持长摘要和证据文本易读。 */
.detail-section ul { padding-left: 1.1rem; } /* 为多条约束证据保留列表层级。 */
.detail-section > .detail-translate-button { margin-top: 0.7rem; } /* 分开摘要原文和翻译按钮。 */
.detail-translated-abstract { margin-top: 0.75rem; padding: 0.75rem 0.85rem; border-left: 3px solid #7eafc4; border-radius: 0 0.55rem 0.55rem 0; background: #f5fafc; } /* 将中文摘要译文与原文明确定义为不同区域。 */
.detail-translated-abstract strong { color: #2e6f95; font-size: 0.72rem; } /* 标记中文摘要说明。 */
.detail-translated-abstract p { margin: 0.4rem 0 0; color: #405b6d; line-height: 1.8; } /* 提供舒适的中文译文行距。 */
.detail-translated-abstract small { display: block; margin-top: 0.45rem; color: #8295a4; font-size: 0.65rem; } /* 标记实际译文模型。 */
.detail-link, .detail-pdf-link { display: inline-block; margin-top: 1.35rem; padding: 0.55rem 0.75rem; border-radius: 0.55rem; font-size: 0.72rem; font-weight: 800; text-decoration: none; } /* 提供一致的合法访问入口按钮。 */
.detail-link { color: #fff; background: #2e6f95; } /* DOI 是详情页的主要访问入口。 */
.detail-pdf-link { margin-left: 0.55rem; color: #28745a; background: #e8f7f0; } /* 公开 PDF 保持为不替代 DOI 的独立入口。 */
</style>
