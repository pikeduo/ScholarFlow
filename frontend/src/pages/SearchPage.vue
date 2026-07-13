<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue' // 管理搜索表单、恢复轮询生命周期和结果派生数据。

import PaperResultCard from '../components/PaperResultCard.vue' // 展示单篇证据化论文结果。
import QueryIntentPanel from '../components/QueryIntentPanel.vue' // 展示并编辑后端真实查询计划。
import SearchStats from '../components/SearchStats.vue' // 展示多源检索与排序阶段统计。
import { LibraryApiError, saveLibraryPaper } from '../services/libraryApi.js' // 将搜索结果保存到个人文献库。
import { SearchApiError, restoreSearchRun, streamSearchPapers, streamSearchWithIntent } from '../services/searchApi.js' // 使用 SSE 执行搜索，并按 run_id 恢复已保存运行。

const examples = [ // 提供可直接填入搜索框的复杂查询示例。
  '近五年使用大语言模型进行多变量时间序列预测，并在 ETT 数据集上实验的论文，排除综述', // 覆盖方法、任务、数据集、年份和排除条件。
  '检索视觉语言模型在医学影像报告生成中的最新研究，优先包含公开数据集', // 覆盖跨领域方法与软偏好。
  'Large language models for scientific literature retrieval and evidence-grounded recommendation', // 提供英文跨语言搜索示例。
]

const form = reactive({ // 保存搜索页当前可编辑查询条件。
  queryText: '', // 保存自然语言研究问题。
  searchMode: 'standard', // 默认使用成本更低的标准模式。
  startYear: '', // 保存可选起始年份。
  endYear: '', // 保存可选结束年份。
  mustInclude: '', // 保存逗号分隔的必须词。
  shouldInclude: '', // 保存逗号分隔的偏好词。
  exclude: '', // 保存逗号分隔的排除词。
  domains: '', // 保存用于动态来源路由的领域标签。
  requiresWebEvidence: false, // 默认不启用网页补充发现。
})

const result = ref(null) // 保存最近一次成功的 MultiRoundSearchResult。
const loading = ref(false) // 标记当前是否正在等待完整检索链路。
const errorMessage = ref('') // 保存可安全展示的请求或校验错误。
const submittedQuery = ref('') // 保存最近一次成功提交的查询文本。
const showAdvanced = ref(false) // 控制高级约束面板展开状态。
const savedPaperIds = ref(new Set()) // 保存当前页面已成功收藏的论文 ID。
const savingPaperIds = ref(new Set()) // 保存收藏请求中的论文 ID，防止重复点击。
const libraryMessage = ref({ text: '', tone: 'success' }) // 保存收藏操作反馈。
const progressEvent = ref(null) // 保存最近一条不含查询正文的 SSE 进度事件。
const recoveryMessage = ref('') // 保存刷新页面后恢复运行状态的中性提示。
const RECOVERY_POLL_INTERVAL_MS = 3000 // 使用短周期只读轮询作为刷新后无法重连 POST SSE 的回退。
const RECOVERY_POLL_MAX_ATTEMPTS = 20 // 最多轮询一分钟，避免异常状态无限占用浏览器和后端资源。
let recoveryPollTimer = null // 保存当前待执行轮询定时器，便于新搜索和卸载时取消。
let recoveryRunId = '' // 保存当前允许轮询的运行标识，防止旧响应覆盖新搜索。
let recoveryPollAttempts = 0 // 记录当前恢复运行的已执行轮询次数。

const routeSources = computed(() => result.value?.run_state?.selected_sources || result.value?.route_plan?.academic_sources || []) // 优先提取多轮实际参与的学术来源并兼容旧响应。
const conditionChips = ref([]) // 保存最近一次成功提交的条件标签，避免后续编辑表单改变旧结果说明。

const discoveries = computed(() => result.value?.discoveries || []) // 保持补充网页发现与论文结果独立展示。
const runState = computed(() => result.value?.run_state || null) // 提取多轮运行状态供搜索页展示过程和停止原因。
const coverageReport = computed(() => result.value?.coverage_report || runState.value?.coverage_report || null) // 提取累计候选覆盖报告并兼容后续状态存储。
const planningMeta = computed(() => ({ // 将后端查询规划观测字段映射为面板属性。
  modelName: result.value?.query_planning_model_name || null, // 自然入口展示实际模型，直接重搜时为空。
  promptTokens: result.value?.query_planning_prompt_tokens || 0, // 展示规划输入 Token。
  completionTokens: result.value?.query_planning_completion_tokens || 0, // 展示规划输出 Token。
  durationMs: result.value?.query_planning_duration_ms || 0, // 展示规划耗时。
}))

function useExample(example) { // 将示例查询填入输入框但不自动发起外部调用。
  form.queryText = example // 允许用户继续编辑示例。
  errorMessage.value = '' // 清除此前表单错误。
}

function buildConditionChipsFromIntent(intent) { // 将已保存 QueryIntent 转换为恢复结果所需的条件标签。
  return buildConditionChips({ // 复用与新提交结果一致的标签规则。
    searchMode: intent.search_mode, // 回显保存的搜索模式。
    startYear: intent.year_range?.[0] || '', // 回显保存的起始年份。
    endYear: intent.year_range?.[1] || '', // 回显保存的结束年份。
    mustInclude: (intent.must_include || []).join(', '), // 回显保存的硬约束。
    shouldInclude: (intent.should_include || []).join(', '), // 回显保存的软约束。
    exclude: (intent.exclude || []).join(', '), // 回显保存的排除条件。
  })
}

function syncRunIdToUrl(runId) { // 将可恢复运行标识写入当前地址而不产生额外历史记录。
  if (!runId || !globalThis.location?.href || !globalThis.history?.replaceState) return // 非浏览器或缺少历史 API 时保持页面功能可用。
  const url = new URL(globalThis.location.href) // 从当前地址构造可安全修改的 URL 对象。
  url.searchParams.set('run_id', runId) // 仅保存不可猜测的运行标识，不写入完整研究问题。
  globalThis.history.replaceState(null, '', url) // 使用替换避免每条 SSE 事件污染浏览历史。
}

function stopRecoveryPolling() { // 停止当前恢复轮询，避免旧运行在新搜索或页面卸载后继续更新状态。
  if (recoveryPollTimer !== null) globalThis.clearTimeout?.(recoveryPollTimer) // 清除尚未触发的只读轮询定时器。
  recoveryPollTimer = null // 重置定时器标识。
  recoveryRunId = '' // 取消当前运行标识授权。
  recoveryPollAttempts = 0 // 清除旧运行重试计数。
}

function applyRecoveredRun(recovered) { // 将只读恢复响应统一映射为页面状态和中性提示。
  const { state, result: recoveredResult } = recovered // 解构轻量状态与可选最终结果。
  progressEvent.value = { run_id: state.run_id, current_round: state.current_round || 0, progress: state.status === 'completed' ? 1 : 0, message: `已恢复搜索运行：${state.status}` } // 使用安全状态字段更新过程提示。
  submittedQuery.value = state.query_intent.original_query // 回显关联研究问题而不修改用户当前输入表单。
  if (recoveredResult) { // 仅当结果接口真实返回时才替换论文集合。
    result.value = recoveredResult // 使用同次持久化结果，绝不重新检索。
    conditionChips.value = buildConditionChipsFromIntent(recoveredResult.query_intent) // 使用保存意图恢复结果条件标签。
    recoveryMessage.value = '已从保存的搜索运行恢复结果' // 说明当前页面没有产生新的检索调用。
    return true // 通知调用方停止轮询。
  }
  recoveryMessage.value = '已恢复搜索运行状态；正在等待最终结果' // 明确当前只恢复状态，不伪造论文集合。
  return false // 通知调用方在需要时继续状态轮询。
}

function shouldPollRecoveredRun(state, hasResult) { // 仅为尚未拿到结果的运行保留有限轮询。
  if (hasResult || state.status === 'failed') return false // 已获得结果或明确失败时不继续请求。
  return ['queued', 'running', 'completed'].includes(state.status) // completed 但结果暂未写入时允许短暂等待持久化完成。
}

function scheduleRecoveryPoll(runId) { // 安排下一次只读状态轮询。
  if (recoveryPollTimer !== null || recoveryRunId !== runId) return // 防止同一运行创建重复定时器或旧运行继续排队。
  if (recoveryPollAttempts >= RECOVERY_POLL_MAX_ATTEMPTS) { // 达到一分钟上限时停止，避免页面无限占用资源。
    recoveryMessage.value = '已恢复搜索运行状态；结果仍未就绪，请稍后刷新本页查看' // 给出可执行且不误导的用户提示。
    stopRecoveryPolling() // 结束有限轮询回退。
    return // 不再创建新定时器。
  }
  recoveryPollTimer = globalThis.setTimeout?.(() => { // 使用浏览器定时器等待下一次状态检查。
    recoveryPollTimer = null // 回调开始时释放标识，允许后续安排下一轮。
    void pollRecoveredRun(runId) // 不阻塞界面响应地执行只读恢复请求。
  }, RECOVERY_POLL_INTERVAL_MS) ?? null // 非浏览器环境缺少定时器时安全禁用轮询。
}

async function pollRecoveredRun(runId) { // 查询已恢复运行的最新状态，并在终态读取同次结果。
  if (recoveryRunId !== runId) return // 用户已提交新搜索或页面卸载时忽略旧运行回调。
  recoveryPollAttempts += 1 // 记录本次只读轮询次数。
  try { // 复用同一 REST 恢复边界，绝不调用 SSE 提交接口。
    const recovered = await restoreSearchRun(runId) // 读取最新轻量状态，并在终态尝试读取同次结果。
    const hasResult = applyRecoveredRun(recovered) // 将最新状态安全映射到页面。
    if (shouldPollRecoveredRun(recovered.state, hasResult)) scheduleRecoveryPoll(runId) // 仅在结果尚未就绪时继续有限轮询。
    else stopRecoveryPolling() // 完成、失败或非预期状态时释放轮询资源。
  } catch (error) { // 临时网络或状态读取错误不应立即丢弃可恢复运行。
    recoveryMessage.value = error instanceof SearchApiError ? `恢复状态轮询失败：${error.message}` : '恢复状态轮询暂时不可用，将稍后重试' // 展示已净化的非阻塞提示。
    if (recoveryRunId === runId) scheduleRecoveryPoll(runId) // 在次数上限内保守重试只读请求。
  }
}

function startRecoveryPolling(runId) { // 为首次恢复到的运行启动一条有限且可取消的轮询链。
  stopRecoveryPolling() // 清除可能由热更新或旧 URL 留下的轮询。
  recoveryRunId = runId // 授权仅此运行可以更新页面状态。
  scheduleRecoveryPoll(runId) // 首次延迟后发起只读状态检查。
}

async function restoreRunFromUrl() { // 在页面首次挂载时恢复已有 run_id 对应的状态或最终结果。
  const runId = new URLSearchParams(globalThis.location?.search || '').get('run_id')?.trim() // 只读取 URL 查询参数中的稳定运行标识。
  if (!runId) return // 普通首次访问保持空白搜索页。
  loading.value = true // 显示恢复中状态并防止用户同时发起新搜索。
  errorMessage.value = '' // 清除可能由热更新保留的旧错误。
  recoveryMessage.value = '' // 清除旧恢复提示。
  try { // 通过只读 REST 恢复状态，不重新调用 Query Agent 或学术来源。
    const recovered = await restoreSearchRun(runId) // 先读取轻量状态，终态再读取完整结果。
    const hasResult = applyRecoveredRun(recovered) // 将恢复状态或最终结果映射到页面。
    if (shouldPollRecoveredRun(recovered.state, hasResult)) startRecoveryPolling(runId) // 运行中或终态结果尚未落库时启用有限轮询回退。
  } catch (error) { // 将不存在运行或读取失败转换为安全页面提示。
    errorMessage.value = error instanceof SearchApiError ? error.message : '恢复已保存的搜索运行时出现未知错误，请稍后重试' // 不展示内部路径或响应正文。
  } finally { // 无论恢复成功或失败都恢复表单操作。
    loading.value = false // 结束恢复加载状态。
  }
}

function buildConditionChips(formSnapshot) { // 将已提交表单快照转换为结果区可回顾标签。
  const chips = [{ label: formSnapshot.searchMode === 'deep' ? '深度模式' : '标准模式', tone: 'neutral' }] // 始终展示搜索模式。
  if (formSnapshot.startYear && formSnapshot.endYear) chips.push({ label: `${formSnapshot.startYear}–${formSnapshot.endYear}`, tone: 'neutral' }) // 展示年份范围。
  if (formSnapshot.mustInclude) chips.push({ label: `必须：${formSnapshot.mustInclude}`, tone: 'positive' }) // 展示硬约束。
  if (formSnapshot.shouldInclude) chips.push({ label: `优先：${formSnapshot.shouldInclude}`, tone: 'neutral' }) // 展示软偏好。
  if (formSnapshot.exclude) chips.push({ label: `排除：${formSnapshot.exclude}`, tone: 'negative' }) // 展示排除条件。
  return chips // 返回保持表单顺序的标签列表。
}

async function submitSearch() { // 执行完整多源检索并更新页面状态。
  if (loading.value) return // 防止重复点击产生并发外部请求。
  stopRecoveryPolling() // 防止旧运行轮询在新搜索期间覆盖页面结果。
  loading.value = true // 立即禁用提交按钮并展示进度。
  errorMessage.value = '' // 清除上一轮错误。
  progressEvent.value = null // 清除上一轮检索残留的过程提示。
  try { // 捕获 API 客户端已净化错误。
    const formSnapshot = { ...form } // 固定本次请求及结果说明使用的表单快照。
    const nextResult = await streamSearchPapers(formSnapshot, handleProgressEvent) // 在同次自然语言多轮检索中实时消费进度并最终读取结果。
    result.value = nextResult // 仅在成功解析响应后替换已有结果。
    submittedQuery.value = formSnapshot.queryText.trim() // 保存结果对应查询供标题回显。
    conditionChips.value = buildConditionChips(formSnapshot) // 保存与当前结果严格对应的条件标签。
    recoveryMessage.value = '' // 新搜索成功后清除旧运行恢复提示。
  } catch (error) { // 将已知和未知错误转换为安全界面消息。
    errorMessage.value = error instanceof SearchApiError ? error.message : '检索过程中出现未知错误，请稍后重试' // 不展示堆栈或响应正文。
  } finally { // 无论成功失败都恢复表单操作。
    loading.value = false // 结束加载状态。
  }
}

async function resubmitIntent(editedIntent) { // 使用编辑后的完整 QueryIntent 跳过 Query Agent 重搜。
  if (loading.value) return // 防止重复点击产生并发外部请求。
  stopRecoveryPolling() // 防止旧运行轮询在编辑重搜期间覆盖页面结果。
  loading.value = true // 禁用搜索和编辑面板。
  errorMessage.value = '' // 清除上一轮错误。
  progressEvent.value = null // 清除编辑前旧运行的进度提示。
  try { // 捕获统一 API 客户端错误。
    const nextResult = await streamSearchWithIntent(editedIntent, handleProgressEvent) // 跳过 Query Agent 并实时消费同次多轮检索进度。
    result.value = nextResult // 成功后替换论文和检索统计。
    submittedQuery.value = editedIntent.original_query // 保持结果对应的原始研究问题。
    conditionChips.value = buildConditionChips({ // 使用编辑后的关键约束更新结果说明。
      searchMode: editedIntent.search_mode, // 映射搜索模式。
      startYear: editedIntent.year_range?.[0] || '', // 映射起始年份。
      endYear: editedIntent.year_range?.[1] || '', // 映射结束年份。
      mustInclude: (editedIntent.must_include || []).join(', '), // 映射硬约束。
      shouldInclude: (editedIntent.should_include || []).join(', '), // 映射软偏好。
      exclude: (editedIntent.exclude || []).join(', '), // 映射排除条件。
    })
    recoveryMessage.value = '' // 编辑重搜成功后清除旧运行恢复提示。
  } catch (error) { // 将已知和未知错误转换为安全界面消息。
    errorMessage.value = error instanceof SearchApiError ? error.message : '重新检索过程中出现未知错误，请稍后重试' // 不展示内部堆栈。
  } finally { // 无论成功失败都恢复交互。
    loading.value = false // 结束加载状态。
  }
}

function handleProgressEvent(event) { // 接收客户端已校验的 SSE 事件并更新加载区轻量状态。
  progressEvent.value = event // 仅保存运行标识、轮次、数量和安全消息。
  if (typeof event.run_id === 'string') syncRunIdToUrl(event.run_id) // 首个事件到达后更新地址以支持刷新恢复。
}

onMounted(() => { // Vue 页面首次显示后读取地址中的可恢复运行标识。
  void restoreRunFromUrl() // 恢复过程不阻塞首屏挂载或输入框渲染。
})

onBeforeUnmount(() => { // 页面切换到文献库或应用卸载时释放恢复轮询。
  stopRecoveryPolling() // 防止已卸载组件继续发起状态读取或更新响应式状态。
})

async function savePaper(paper) { // 将单篇搜索结果去重保存到个人文献库。
  if (savingPaperIds.value.has(paper.paper_id) || savedPaperIds.value.has(paper.paper_id)) return // 防止同一卡片重复提交。
  savingPaperIds.value.add(paper.paper_id) // 立即标记请求中状态。
  libraryMessage.value = { text: '', tone: 'success' } // 清除上一条收藏反馈。
  try { // 将 API 客户端公共错误转换为页面提示。
    const saveResult = await saveLibraryPaper(paper) // 默认以未读、无标签状态收藏论文。
    savedPaperIds.value.add(paper.paper_id) // 成功后固定当前卡片已收藏状态。
    libraryMessage.value = { text: saveResult.created ? '论文已收藏到“我的文献库”' : '该论文已在文献库中，元数据已更新', tone: 'success' } // 区分新建与去重命中。
  } catch (error) { // 捕获断网、后端错误和响应契约异常。
    libraryMessage.value = { text: error instanceof LibraryApiError ? error.message : '收藏论文时出现未知错误，请稍后重试', tone: 'error' } // 不展示底层堆栈。
  } finally { // 无论成功失败都恢复按钮。
    savingPaperIds.value.delete(paper.paper_id) // 清除请求中状态以允许失败重试。
  }
}
</script>

<template>
  <div class="search-page">
    <!-- 首屏聚焦复杂研究问题输入，并将高级条件保持为按需展开。 -->
    <section class="search-hero" aria-labelledby="search-title">
      <div class="hero-copy">
        <p class="eyebrow">MULTI-SOURCE ACADEMIC DISCOVERY</p>
        <h1 id="search-title">把复杂研究问题，<br><em>编织</em>成论文脉络。</h1>
        <p>描述你的研究目标、方法、数据集与限制条件。研索将跨来源召回论文，并逐层过滤、排序和核验证据。</p>
      </div>
      <form class="search-panel" aria-label="复杂文献搜索" @submit.prevent="submitSearch">
        <label for="research-query">研究问题</label>
        <div class="query-box">
          <textarea id="research-query" v-model="form.queryText" rows="4" maxlength="1200" placeholder="例如：查找近五年使用大语言模型进行多变量时间序列预测，并在 ETT 数据集上实验的论文，排除综述。" :disabled="loading"></textarea>
          <div class="query-actions">
            <span>{{ form.queryText.length }} / 1200</span>
            <button class="search-button" type="submit" :disabled="loading">
              <span v-if="loading" class="spinner" aria-hidden="true"></span>
              {{ loading ? '正在编织检索结果…' : '开始检索' }}
            </button>
          </div>
        </div>
        <div class="mode-row">
          <fieldset>
            <legend>检索模式</legend>
            <label :class="['mode-option', { 'is-selected': form.searchMode === 'standard' }]">
              <input v-model="form.searchMode" type="radio" value="standard" :disabled="loading">
              <span><strong>标准</strong><small>1–2 轮 · 更快</small></span>
            </label>
            <label :class="['mode-option', { 'is-selected': form.searchMode === 'deep' }]">
              <input v-model="form.searchMode" type="radio" value="deep" :disabled="loading">
              <span><strong>深度</strong><small>最多 3 轮 · 补足缺口</small></span>
            </label>
          </fieldset>
          <button class="advanced-toggle" type="button" :aria-expanded="showAdvanced" @click="showAdvanced = !showAdvanced">
            {{ showAdvanced ? '收起约束条件' : '添加约束条件' }}
            <span aria-hidden="true">{{ showAdvanced ? '−' : '+' }}</span>
          </button>
        </div>
        <div v-if="showAdvanced" class="advanced-fields">
          <div class="field-group year-group">
            <label>发表年份</label>
            <div><input v-model="form.startYear" type="number" min="1800" max="2100" placeholder="起始" :disabled="loading"><span>至</span><input v-model="form.endYear" type="number" min="1800" max="2100" placeholder="结束" :disabled="loading"></div>
          </div>
          <div class="field-group">
            <label for="must-include">必须包含</label>
            <input id="must-include" v-model="form.mustInclude" type="text" placeholder="Transformer, ETT" :disabled="loading">
          </div>
          <div class="field-group">
            <label for="should-include">优先包含</label>
            <input id="should-include" v-model="form.shouldInclude" type="text" placeholder="开源代码, benchmark" :disabled="loading">
          </div>
          <div class="field-group">
            <label for="exclude">排除</label>
            <input id="exclude" v-model="form.exclude" type="text" placeholder="survey, review" :disabled="loading">
          </div>
          <div class="field-group">
            <label for="domains">领域标签</label>
            <input id="domains" v-model="form.domains" type="text" placeholder="machine learning, computer science" :disabled="loading">
          </div>
          <label class="web-evidence-option">
            <input v-model="form.requiresWebEvidence" type="checkbox" :disabled="loading">
            <span><strong>补充网页证据</strong><small>Tavily 结果将独立展示，不会伪装成论文。</small></span>
          </label>
        </div>
      <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>
      <p v-if="recoveryMessage" class="recovery-message" role="status">{{ recoveryMessage }}</p>
      </form>
      <div class="example-row" aria-label="示例查询">
        <span>试试这些问题</span>
        <button v-for="(example, index) in examples" :key="example" type="button" :disabled="loading" @click="useExample(example)">示例 {{ index + 1 }}</button>
      </div>
    </section>

    <!-- 请求进行中时使用阶段提示保持用户对长模型链路的预期。 -->
    <section v-if="loading" class="loading-state" aria-live="polite">
      <div class="loading-orbit" aria-hidden="true"><span></span><i></i></div>
      <div>
        <strong>{{ progressEvent?.message || '正在执行多源论文检索' }}</strong>
        <p>{{ progressEvent ? `第 ${progressEvent.current_round ?? 0} 轮 · ${Math.round((progressEvent.progress || 0) * 100)}% · ${progressEvent.metrics?.candidate_count ?? 0} 篇累计候选` : 'OpenAlex / Semantic Scholar → 身份融合 → BGE-M3 → Cross Encoder → LLM 核验 → 覆盖缺口分析' }}</p>
      </div>
    </section>

    <!-- 成功响应后展示可审计检索轨迹和证据化论文。 -->
    <section v-if="result && !loading" class="results-shell" aria-labelledby="results-title">
      <SearchStats :result="result" />
      <section v-if="runState" class="run-summary" aria-labelledby="run-summary-title">
        <div>
          <p class="eyebrow">MULTI-ROUND CONTROL</p>
          <h2 id="run-summary-title">多轮搜索状态</h2>
          <p>{{ `已完成 ${runState.current_round} / ${runState.max_rounds} 轮，${runState.stop_reason || '正在汇总结果'}` }}</p>
        </div>
        <div v-if="coverageReport" class="coverage-summary">
          <span>高相关 {{ coverageReport.high_relevance_count }} / {{ coverageReport.target_count }}</span>
          <span>部分相关 {{ coverageReport.partial_relevance_count }}</span>
          <span>边际收益 {{ Math.round((coverageReport.marginal_gain || 0) * 100) }}%</span>
        </div>
        <ul v-if="coverageReport?.gaps?.length" class="coverage-gap-list" aria-label="尚未覆盖的检索缺口">
          <li v-for="gap in coverageReport.gaps" :key="`${gap.gap_type}-${gap.constraint}`">{{ `${gap.constraint}：当前 ${gap.current_match_count} 篇，建议补充检索“${gap.recommended_query_focus}”` }}</li>
        </ul>
      </section>
      <QueryIntentPanel v-if="result.query_intent" :intent="result.query_intent" :planning-meta="planningMeta" :disabled="loading" @resubmit="resubmitIntent" />
      <header class="results-header">
        <div>
          <p class="eyebrow">EVIDENCE-GROUNDED RESULTS</p>
          <h2 id="results-title">最终推荐 <span>{{ result.papers.length }}</span></h2>
          <p class="submitted-query">“{{ submittedQuery }}”</p>
        </div>
        <div class="route-summary" aria-label="本次检索来源">
          <span v-for="source in routeSources" :key="source">{{ source }}</span>
        </div>
      </header>
      <div class="condition-row" aria-label="当前检索条件">
        <span v-for="chip in conditionChips" :key="chip.label" :class="['condition-chip', `is-${chip.tone}`]">{{ chip.label }}</span>
      </div>
      <p v-if="libraryMessage.text" :class="['library-message', `is-${libraryMessage.tone}`]" role="status">{{ libraryMessage.text }}</p>
      <div v-if="result.papers.length" class="paper-list">
        <PaperResultCard v-for="(paper, index) in result.papers" :key="paper.paper_id" :paper="paper" :rank="index + 1" :saved="savedPaperIds.has(paper.paper_id)" :saving="savingPaperIds.has(paper.paper_id)" @save="savePaper" />
      </div>
      <div v-else class="empty-state">
        <strong>暂未找到满足全部条件的论文</strong>
        <p>可以放宽年份、必须词或排除条件后重新检索。</p>
      </div>
      <section v-if="discoveries.length" class="discovery-section" aria-labelledby="discovery-title">
        <div>
          <p class="eyebrow">SUPPLEMENTAL WEB EVIDENCE</p>
          <h2 id="discovery-title">补充网页发现</h2>
          <p>以下内容仅作为网页证据，不参与论文身份融合、引用关系或学术排序。</p>
        </div>
        <ul>
          <li v-for="item in discoveries" :key="item.url">
            <a :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.title }}</a>
            <span>{{ item.snippet || item.url }}</span>
          </li>
        </ul>
      </section>
    </section>
  </div>
</template>

<style scoped>
.search-page { /* 建立搜索页背景与内容留白。 */
  min-height: calc(100vh - 4.5rem); /* 填充顶栏以下视口。 */
  padding-bottom: 5rem; /* 为长结果列表保留底部呼吸。 */
  background: radial-gradient(circle at 10% 0%, rgba(126, 178, 193, 0.18), transparent 28rem), linear-gradient(180deg, #f8fafc 0%, #f3f7f8 48%, #f8fafc 100%); /* 使用克制渐变建立研究工作台氛围。 */
}

.search-hero { /* 承载页面定位、搜索表单和示例。 */
  width: min(1120px, calc(100% - 2rem)); /* 限制宽屏行长并保留手机边距。 */
  margin: 0 auto; /* 水平居中首屏内容。 */
  padding: clamp(3.5rem, 8vw, 6.5rem) 0 2.5rem; /* 创建充足但响应式的顶部空间。 */
}

.hero-copy { /* 控制主标题阅读宽度。 */
  max-width: 51rem; /* 避免标题和描述过长。 */
}

.eyebrow { /* 显示区块英文辅助标签。 */
  margin: 0 0 0.6rem; /* 与标题形成紧凑组合。 */
  color: #2e6f95; /* 使用品牌强调色。 */
  font-size: 0.68rem; /* 降低辅助标题权重。 */
  font-weight: 800; /* 保证小字号清晰。 */
  letter-spacing: 0.18em; /* 建立标签视觉。 */
}

h1 { /* 设置搜索页主标题。 */
  margin: 0; /* 清除默认标题间距。 */
  color: #16324a; /* 使用沉稳深蓝。 */
  font-family: Georgia, "Noto Serif SC", "Songti SC", serif; /* 形成学术出版气质。 */
  font-size: clamp(2.3rem, 5.4vw, 4.35rem); /* 在手机与桌面平滑缩放。 */
  font-weight: 500; /* 保持标题克制优雅。 */
  letter-spacing: -0.035em; /* 收紧大字号英文和中文间距。 */
  line-height: 1.16; /* 保持双行标题紧凑。 */
}

h1 em { /* 突出“编织”产品隐喻。 */
  position: relative; /* 为装饰底纹提供上下文。 */
  color: #2e6f95; /* 使用品牌蓝绿色。 */
  font-style: normal; /* 避免中文斜体失真。 */
}

.hero-copy > p:last-child { /* 设置主标题下的产品说明。 */
  max-width: 43rem; /* 控制说明行长。 */
  margin: 1.15rem 0 0; /* 与主标题拉开层级。 */
  color: #607487; /* 使用次级正文色。 */
  font-size: 0.93rem; /* 保持说明清晰。 */
  line-height: 1.8; /* 提升中文段落可读性。 */
}

.search-panel { /* 构建查询输入的主要交互面板。 */
  margin-top: 2rem; /* 与产品说明分隔。 */
  padding: clamp(1rem, 2.5vw, 1.5rem); /* 在不同屏幕调整内部留白。 */
  border: 1px solid rgba(184, 204, 220, 0.82); /* 使用品牌蓝灰描边。 */
  border-radius: 1.35rem; /* 形成工作台式大面板。 */
  background: rgba(255, 255, 255, 0.94); /* 保持输入区域清晰。 */
  box-shadow: 0 24px 60px rgba(31, 67, 94, 0.11); /* 将搜索框置于页面视觉前景。 */
}

.search-panel > label { /* 标记主查询输入。 */
  display: block; /* 独占一行。 */
  margin-bottom: 0.6rem; /* 与输入框分隔。 */
  color: #334e68; /* 使用正文深色。 */
  font-size: 0.78rem; /* 保持表单标签紧凑。 */
  font-weight: 800; /* 提升字段辨识度。 */
}

.query-box { /* 组合文本域与底部操作栏。 */
  overflow: hidden; /* 保持输入背景在圆角内。 */
  border: 1px solid #cad8e3; /* 清晰标记主要输入区域。 */
  border-radius: 0.95rem; /* 使用内层圆角。 */
  background: #fbfdfe; /* 使用轻微冷白底色。 */
  transition: border-color 160ms ease, box-shadow 160ms ease; /* 平滑焦点反馈。 */
}

.query-box:focus-within { /* 强化键盘或鼠标输入焦点。 */
  border-color: #5d9ab4; /* 使用品牌交互色。 */
  box-shadow: 0 0 0 4px rgba(93, 154, 180, 0.12); /* 提供无障碍焦点外圈。 */
}

textarea { /* 设置自然语言查询输入。 */
  display: block; /* 消除行内元素底部间隙。 */
  width: 100%; /* 填满查询面板。 */
  min-height: 7.5rem; /* 为复杂研究问题保留空间。 */
  resize: vertical; /* 允许用户按需增加高度。 */
  padding: 1rem 1.1rem; /* 提供舒适输入留白。 */
  border: 0; /* 由外层 query-box 统一绘制边框。 */
  outline: 0; /* 使用 focus-within 替代默认轮廓。 */
  color: #17324d; /* 使用高对比输入文字。 */
  background: transparent; /* 继承查询框背景。 */
  font: inherit; /* 使用全局中文字体。 */
  font-size: 0.95rem; /* 保持长查询易读。 */
  line-height: 1.7; /* 提升多行输入体验。 */
}

textarea::placeholder { /* 设置查询示例占位。 */
  color: #9aaaba; /* 降低占位文本权重。 */
}

.query-actions { /* 横向排列字符计数与提交按钮。 */
  display: flex; /* 使用弹性布局。 */
  align-items: center; /* 垂直居中。 */
  justify-content: space-between; /* 分置计数与按钮。 */
  gap: 1rem; /* 避免内容相贴。 */
  padding: 0.7rem 0.75rem 0.75rem 1.1rem; /* 对齐文本域左右留白。 */
  border-top: 1px solid #e7edf2; /* 分隔输入与操作区。 */
}

.query-actions > span { /* 显示字符计数。 */
  color: #a0adba; /* 使用低权重文字。 */
  font-size: 0.66rem; /* 避免干扰提交操作。 */
}

.search-button { /* 设置主提交按钮。 */
  display: inline-flex; /* 对齐加载图标和文字。 */
  min-width: 9.5rem; /* 防止加载文案改变按钮宽度过大。 */
  min-height: 2.65rem; /* 提供舒适点击区域。 */
  align-items: center; /* 垂直居中内容。 */
  justify-content: center; /* 水平居中内容。 */
  gap: 0.55rem; /* 分隔加载图标和文字。 */
  padding: 0.65rem 1.25rem; /* 建立主操作按钮体积。 */
  border: 0; /* 使用背景定义按钮边界。 */
  border-radius: 0.72rem; /* 与面板圆角协调。 */
  color: #ffffff; /* 提升主按钮对比度。 */
  background: linear-gradient(135deg, #173f7a, #2e6f95); /* 使用品牌渐变。 */
  box-shadow: 0 8px 18px rgba(23, 63, 122, 0.2); /* 突出主要操作。 */
  cursor: pointer; /* 告知用户可点击。 */
  font: inherit; /* 使用全局字体。 */
  font-size: 0.8rem; /* 保持按钮紧凑。 */
  font-weight: 800; /* 强调提交动作。 */
}

.search-button:disabled { /* 表达检索中的禁用状态。 */
  cursor: wait; /* 告知用户正在处理。 */
  opacity: 0.72; /* 降低禁用按钮强度。 */
}

.spinner { /* 绘制按钮内加载环。 */
  width: 0.9rem; /* 固定加载环尺寸。 */
  height: 0.9rem; /* 保持圆形比例。 */
  border: 2px solid rgba(255, 255, 255, 0.35); /* 绘制浅色环底。 */
  border-top-color: #ffffff; /* 使用实色顶部形成旋转感。 */
  border-radius: 50%; /* 将边框变为圆环。 */
  animation: spin 800ms linear infinite; /* 持续旋转表示等待。 */
}

.mode-row { /* 横向排列模式选择和高级条件开关。 */
  display: flex; /* 使用弹性布局。 */
  align-items: end; /* 对齐控件底部。 */
  justify-content: space-between; /* 分置模式和高级入口。 */
  gap: 1rem; /* 为窄屏换行保留间距。 */
  margin-top: 1rem; /* 与主查询框分隔。 */
}

fieldset { /* 将模式选项组成可访问字段组。 */
  display: flex; /* 横向排列模式。 */
  gap: 0.5rem; /* 分隔两个选项。 */
  margin: 0; /* 移除默认外边距。 */
  padding: 0; /* 移除默认内边距。 */
  border: 0; /* 使用选项卡自身边界。 */
}

legend { /* 标记检索模式字段组。 */
  position: absolute; /* 保留语义但不占布局空间。 */
  width: 1px; /* 缩小视觉区域。 */
  height: 1px; /* 缩小视觉区域。 */
  overflow: hidden; /* 隐藏屏幕视觉内容。 */
  clip-path: inset(50%); /* 仅供屏幕阅读器读取。 */
}

.mode-option { /* 设置单个模式选择卡。 */
  display: inline-flex; /* 横向对齐单选框和说明。 */
  align-items: center; /* 垂直居中。 */
  gap: 0.5rem; /* 分隔控件和文字。 */
  padding: 0.55rem 0.7rem; /* 提供可点击区域。 */
  border: 1px solid #dce5ec; /* 标记选项边界。 */
  border-radius: 0.7rem; /* 使用小圆角。 */
  cursor: pointer; /* 告知用户可选择。 */
}

.mode-option.is-selected { /* 突出当前检索模式。 */
  border-color: #8ab3c5; /* 使用品牌蓝灰。 */
  background: #f0f7fa; /* 使用浅蓝选中底色。 */
}

.mode-option.is-disabled { /* 弱化尚未接入多轮编排的深度模式。 */
  cursor: not-allowed; /* 告知用户当前不可选择。 */
  opacity: 0.52; /* 降低未开放选项权重。 */
}

.mode-option input { /* 设置原生单选框强调色。 */
  accent-color: #2e6f95; /* 与品牌交互色一致。 */
}

.mode-option span { /* 纵向排列模式名称和说明。 */
  display: grid; /* 建立双行结构。 */
  gap: 0.1rem; /* 控制行间距。 */
}

.mode-option strong { /* 显示模式名称。 */
  color: #334e68; /* 使用正文深色。 */
  font-size: 0.72rem; /* 保持模式卡紧凑。 */
}

.mode-option small { /* 显示模式成本说明。 */
  color: #91a0ae; /* 降低辅助信息权重。 */
  font-size: 0.61rem; /* 控制信息密度。 */
}

.advanced-toggle { /* 设置高级约束开关。 */
  display: inline-flex; /* 对齐文字和加减号。 */
  align-items: center; /* 垂直居中。 */
  gap: 0.5rem; /* 分隔文字和图形。 */
  padding: 0.55rem 0; /* 扩大纵向点击区域。 */
  border: 0; /* 使用文字式按钮。 */
  color: #2e6f95; /* 使用品牌交互色。 */
  background: transparent; /* 保持轻量。 */
  cursor: pointer; /* 告知用户可展开。 */
  font: inherit; /* 使用全局字体。 */
  font-size: 0.72rem; /* 作为次级操作。 */
  font-weight: 800; /* 提升可发现性。 */
}

.advanced-toggle span { /* 绘制加减号胶囊。 */
  display: grid; /* 居中符号。 */
  width: 1.25rem; /* 固定图形宽度。 */
  height: 1.25rem; /* 固定图形高度。 */
  place-items: center; /* 完全居中符号。 */
  border-radius: 50%; /* 使用圆形。 */
  background: #eaf3f8; /* 使用浅蓝背景。 */
}

.advanced-fields { /* 组织可选硬约束和路由条件。 */
  display: grid; /* 使用响应式网格。 */
  grid-template-columns: repeat(3, minmax(0, 1fr)); /* 桌面每行三个字段。 */
  gap: 0.9rem; /* 分隔字段。 */
  margin-top: 1rem; /* 与模式行分隔。 */
  padding-top: 1rem; /* 为顶部边线保留空间。 */
  border-top: 1px dashed #d7e2ea; /* 表达可选扩展区域。 */
}

.field-group { /* 纵向排列字段标签与输入。 */
  display: grid; /* 建立字段布局。 */
  gap: 0.4rem; /* 分隔标签和输入。 */
}

.field-group > label { /* 设置高级字段标签。 */
  color: #536b7f; /* 使用次级正文色。 */
  font-size: 0.68rem; /* 保持高级区紧凑。 */
  font-weight: 800; /* 提升字段辨识。 */
}

.field-group input { /* 设置高级条件输入。 */
  width: 100%; /* 填满网格列。 */
  min-width: 0; /* 允许年份输入收缩。 */
  padding: 0.65rem 0.7rem; /* 提供舒适输入空间。 */
  border: 1px solid #d6e1e9; /* 定义字段边界。 */
  border-radius: 0.6rem; /* 使用紧凑圆角。 */
  outline: 0; /* 由焦点边框替代默认轮廓。 */
  color: #334e68; /* 使用正文输入色。 */
  background: #fbfdfe; /* 与主输入保持统一。 */
  font: inherit; /* 使用全局字体。 */
  font-size: 0.72rem; /* 控制字段密度。 */
}

.field-group input:focus { /* 标记高级输入焦点。 */
  border-color: #5d9ab4; /* 使用品牌交互色。 */
  box-shadow: 0 0 0 3px rgba(93, 154, 180, 0.1); /* 提供焦点外圈。 */
}

.year-group > div { /* 横向排列起止年份。 */
  display: flex; /* 使用弹性布局。 */
  align-items: center; /* 垂直居中分隔词。 */
  gap: 0.45rem; /* 分隔两个年份输入。 */
}

.year-group span { /* 设置年份分隔词。 */
  color: #91a0ae; /* 使用辅助文字色。 */
  font-size: 0.65rem; /* 保持紧凑。 */
}

.web-evidence-option { /* 设置网页证据开关卡。 */
  display: flex; /* 横向排列复选框和说明。 */
  align-items: center; /* 垂直居中。 */
  gap: 0.65rem; /* 分隔控件和文字。 */
  padding: 0.55rem 0.7rem; /* 提供可点击区域。 */
  border: 1px solid #dce5ec; /* 与其他字段形成整体。 */
  border-radius: 0.65rem; /* 使用小圆角。 */
  cursor: pointer; /* 告知用户可切换。 */
}

.web-evidence-option input { /* 设置复选框强调色。 */
  accent-color: #2e6f95; /* 与品牌交互色一致。 */
}

.web-evidence-option span { /* 纵向排列开关标题和边界说明。 */
  display: grid; /* 建立双行结构。 */
  gap: 0.15rem; /* 保持文字紧凑。 */
}

.web-evidence-option strong { /* 显示开关名称。 */
  color: #536b7f; /* 使用正文色。 */
  font-size: 0.68rem; /* 匹配高级字段标签。 */
}

.web-evidence-option small { /* 说明网页发现隔离边界。 */
  color: #91a0ae; /* 使用辅助文字色。 */
  font-size: 0.58rem; /* 控制说明密度。 */
}

.form-error { /* 展示前端校验或后端公共错误。 */
  margin: 0.9rem 0 0; /* 与表单控件分隔。 */
  padding: 0.7rem 0.8rem; /* 提供错误提示留白。 */
  border-radius: 0.65rem; /* 使用提示条圆角。 */
  color: #9b3c36; /* 使用克制红色。 */
  background: #fff0ee; /* 使用浅红背景。 */
  font-size: 0.72rem; /* 保持错误可读。 */
}

.recovery-message { /* 展示不会触发重新检索的运行恢复提示。 */
  margin: 0.9rem 0 0; /* 与表单控件和错误提示保持一致间距。 */
  padding: 0.7rem 0.8rem; /* 提供清晰可扫读的提示区域。 */
  border-radius: 0.65rem; /* 与表单错误提示保持视觉一致。 */
  color: #386277; /* 使用中性蓝色而非成功或错误色。 */
  background: #eef6fa; /* 区分于搜索结果和错误状态。 */
  font-size: 0.72rem; /* 保持辅助信息层级。 */
}

.example-row { /* 横向展示查询示例入口。 */
  display: flex; /* 使用弹性布局。 */
  flex-wrap: wrap; /* 窄屏允许换行。 */
  align-items: center; /* 对齐标签和按钮。 */
  gap: 0.45rem; /* 分隔示例入口。 */
  margin-top: 0.85rem; /* 与搜索面板分隔。 */
  padding-left: 0.25rem; /* 视觉对齐搜索面板内容。 */
}

.example-row > span { /* 标记示例区域。 */
  margin-right: 0.2rem; /* 与示例按钮分隔。 */
  color: #8293a5; /* 使用辅助文字色。 */
  font-size: 0.67rem; /* 保持示例为次级入口。 */
}

.example-row button { /* 设置示例查询按钮。 */
  padding: 0.35rem 0.6rem; /* 提供轻量点击区域。 */
  border: 1px solid #d9e4eb; /* 使用浅边框。 */
  border-radius: 999px; /* 形成胶囊入口。 */
  color: #607487; /* 使用次级文字色。 */
  background: rgba(255, 255, 255, 0.72); /* 保持示例轻盈。 */
  cursor: pointer; /* 告知用户可点击。 */
  font: inherit; /* 使用全局字体。 */
  font-size: 0.64rem; /* 控制信息密度。 */
}

.loading-state { /* 展示完整检索链路等待状态。 */
  display: flex; /* 横向排列动画和说明。 */
  width: min(1120px, calc(100% - 2rem)); /* 与主要内容对齐。 */
  align-items: center; /* 垂直居中。 */
  gap: 1.2rem; /* 分隔动画和文字。 */
  margin: 0 auto; /* 水平居中。 */
  padding: 1.5rem; /* 提供状态面板留白。 */
  border: 1px solid #dbe6ed; /* 定义状态边界。 */
  border-radius: 1.1rem; /* 与结果面板保持一致。 */
  background: rgba(255, 255, 255, 0.82); /* 保持轻盈背景。 */
}

.loading-state strong { /* 显示当前加载主状态。 */
  color: #334e68; /* 使用正文深色。 */
  font-size: 0.88rem; /* 保持状态清晰。 */
}

.loading-state p { /* 展示分层检索顺序。 */
  margin: 0.35rem 0 0; /* 与主状态分隔。 */
  color: #8293a5; /* 使用辅助文字色。 */
  font-size: 0.68rem; /* 控制流程信息密度。 */
}

.loading-orbit { /* 绘制无需图片的轨道加载动画。 */
  position: relative; /* 为内部轨道点提供上下文。 */
  width: 2.8rem; /* 固定动画区域宽度。 */
  height: 2.8rem; /* 保持正方形。 */
  flex: 0 0 auto; /* 防止动画区域收缩。 */
  border: 1px solid #b9d1dc; /* 绘制外层轨道。 */
  border-radius: 50%; /* 使用圆形轨道。 */
  animation: spin 2s linear infinite; /* 缓慢旋转轨道。 */
}

.loading-orbit span,
.loading-orbit i { /* 绘制两个轨道节点。 */
  position: absolute; /* 定位到轨道边缘。 */
  width: 0.55rem; /* 固定节点尺寸。 */
  height: 0.55rem; /* 保持节点圆形。 */
  border-radius: 50%; /* 将节点变为圆点。 */
  background: #2e6f95; /* 使用品牌强调色。 */
}

.loading-orbit span { /* 定位第一个轨道节点。 */
  top: -0.22rem; /* 放置在轨道顶部。 */
  left: 1rem; /* 水平居中节点。 */
}

.loading-orbit i { /* 定位第二个轨道节点。 */
  right: -0.15rem; /* 放置在轨道右侧。 */
  bottom: 0.55rem; /* 调整到右下方。 */
  width: 0.36rem; /* 使用次级节点尺寸。 */
  height: 0.36rem; /* 保持次级节点圆形。 */
  background: #70a6b8; /* 使用较浅品牌色。 */
}

.results-shell { /* 承载统计和最终论文列表。 */
  display: grid; /* 纵向组织结果区块。 */
  width: min(1120px, calc(100% - 2rem)); /* 与搜索面板对齐。 */
  gap: 1.6rem; /* 分隔统计、标题和列表。 */
  margin: 0 auto; /* 水平居中。 */
  padding-top: 1.25rem; /* 与首屏搜索区域分隔。 */
}

.results-header { /* 横向排列结果标题与来源。 */
  display: flex; /* 使用弹性布局。 */
  align-items: end; /* 将来源与标题底部对齐。 */
  justify-content: space-between; /* 分置标题和来源。 */
  gap: 1rem; /* 避免窄屏相贴。 */
  padding-top: 0.5rem; /* 增加与统计区距离。 */
}

.run-summary { /* 展示多轮控制器的轮次、停止原因和覆盖缺口。 */
  display: grid; /* 纵向组织主状态、统计胶囊和缺口列表。 */
  gap: 0.8rem; /* 保持不同层级信息清晰分隔。 */
  padding: 1.2rem 1.35rem; /* 为多轮过程摘要提供紧凑留白。 */
  border: 1px solid #cfe0e8; /* 使用浅蓝边框表示过程性信息。 */
  border-radius: 1rem; /* 与搜索统计面板保持一致圆角。 */
  background: #f7fbfc; /* 使用轻量背景避免压过论文结果。 */
}

.run-summary h2 { /* 设置多轮过程标题。 */
  margin: 0; /* 移除默认标题留白。 */
  color: #254a62; /* 使用沉稳蓝色文字。 */
  font-family: Georgia, "Noto Serif SC", serif; /* 延续页面学术排版。 */
  font-size: 1.12rem; /* 保持过程区低于主结果标题。 */
}

.run-summary > div:first-child > p:last-child { /* 展示用户可理解的停止原因。 */
  margin: 0.35rem 0 0; /* 与标题紧凑分隔。 */
  color: #607487; /* 使用辅助正文颜色。 */
  font-size: 0.73rem; /* 控制过程说明信息密度。 */
}

.coverage-summary { /* 横向展示完成度、部分相关和边际收益。 */
  display: flex; /* 使用弹性布局排列统计胶囊。 */
  flex-wrap: wrap; /* 窄屏允许统计换行。 */
  gap: 0.45rem; /* 分隔每项统计。 */
}

.coverage-summary span { /* 设置单个覆盖统计胶囊。 */
  padding: 0.32rem 0.55rem; /* 提供紧凑留白。 */
  border-radius: 999px; /* 使用胶囊表现辅助统计。 */
  color: #386277; /* 使用低饱和蓝色。 */
  background: #e8f2f5; /* 与面板底色拉开层次。 */
  font-size: 0.66rem; /* 保持辅助信息紧凑。 */
  font-weight: 700; /* 提升小字号可读性。 */
}

.coverage-gap-list { /* 列出仍未充分覆盖的可解释约束。 */
  display: grid; /* 纵向排列缺口条目。 */
  gap: 0.35rem; /* 分隔相邻缺口。 */
  margin: 0; /* 清除默认列表外边距。 */
  padding: 0; /* 清除默认列表缩进。 */
  list-style: none; /* 使用提示条而非默认圆点。 */
}

.coverage-gap-list li { /* 设置单条缺口说明。 */
  padding: 0.45rem 0.6rem; /* 提供可扫读的提示条留白。 */
  border-radius: 0.55rem; /* 与统计胶囊形成层级差异。 */
  color: #7a5b2c; /* 使用克制琥珀文字提示尚未覆盖。 */
  background: #fff8e9; /* 使用浅琥珀背景。 */
  font-size: 0.68rem; /* 控制缺口提示密度。 */
  line-height: 1.5; /* 提升较长建议文本可读性。 */
}

.results-header h2,
.discovery-section h2 { /* 设置结果和补充发现标题。 */
  margin: 0; /* 清除默认标题间距。 */
  color: #18354f; /* 使用深蓝主文字。 */
  font-family: Georgia, "Noto Serif SC", serif; /* 延续学术出版风格。 */
  font-size: clamp(1.55rem, 3vw, 2.15rem); /* 响应式调整区块标题。 */
  font-weight: 500; /* 保持克制。 */
}

.results-header h2 span { /* 显示最终结果数量。 */
  margin-left: 0.35rem; /* 与标题分隔。 */
  color: #2e6f95; /* 使用品牌强调色。 */
  font-family: Georgia, serif; /* 使用衬线数字。 */
}

.submitted-query { /* 回显本次查询文本。 */
  max-width: 48rem; /* 控制长查询行宽。 */
  margin: 0.45rem 0 0; /* 与标题分隔。 */
  overflow: hidden; /* 隐藏过长单行查询。 */
  color: #718096; /* 使用辅助正文色。 */
  font-size: 0.76rem; /* 保持查询为上下文信息。 */
  text-overflow: ellipsis; /* 超长查询显示省略号。 */
  white-space: nowrap; /* 保持标题区紧凑。 */
}

.route-summary { /* 展示实际调用的学术来源。 */
  display: flex; /* 横向排列来源。 */
  flex-wrap: wrap; /* 来源较多时允许换行。 */
  justify-content: flex-end; /* 右对齐来源标签。 */
  gap: 0.4rem; /* 分隔来源。 */
}

.route-summary span { /* 设置来源标签。 */
  padding: 0.32rem 0.6rem; /* 形成胶囊。 */
  border: 1px solid #cddce6; /* 使用蓝灰边框。 */
  border-radius: 999px; /* 使用完整圆角。 */
  color: #527185; /* 使用次级蓝色。 */
  background: rgba(255, 255, 255, 0.7); /* 与页面背景区分。 */
  font-size: 0.66rem; /* 保持来源紧凑。 */
  font-weight: 700; /* 提升英文来源可读性。 */
}

.condition-row { /* 展示当前查询硬软约束。 */
  display: flex; /* 横向排列标签。 */
  flex-wrap: wrap; /* 条件较多时自动换行。 */
  gap: 0.4rem; /* 分隔条件。 */
  margin-top: -0.8rem; /* 拉近标题与条件关联。 */
}

.condition-chip { /* 设置通用条件胶囊。 */
  padding: 0.32rem 0.55rem; /* 提供紧凑留白。 */
  border-radius: 0.45rem; /* 区别于来源圆形胶囊。 */
  color: #607487; /* 默认使用次级文字。 */
  background: #e9eef2; /* 默认使用中性背景。 */
  font-size: 0.65rem; /* 控制条件密度。 */
}

.condition-chip.is-positive { /* 标记必须满足条件。 */
  color: #28745a; /* 使用绿色文字。 */
  background: #e8f7f0; /* 使用浅绿背景。 */
}

.condition-chip.is-negative { /* 标记排除条件。 */
  color: #9b4b45; /* 使用克制红色文字。 */
  background: #faecea; /* 使用浅红背景。 */
}

.paper-list { /* 纵向排列最终论文卡片。 */
  display: grid; /* 使用网格控制间距。 */
  gap: 0.85rem; /* 分隔论文结果。 */
}

.library-message { /* 展示收藏操作结果。 */
  margin: -0.7rem 0 0; /* 拉近检索条件与操作反馈。 */
  padding: 0.65rem 0.75rem; /* 提供提示条留白。 */
  border-radius: 0.65rem; /* 与页面提示样式协调。 */
  font-size: 0.72rem; /* 保持反馈清晰但不抢占结果标题。 */
}

.library-message.is-success { /* 标记收藏成功或去重命中。 */
  color: #28745a; /* 使用可信绿色文字。 */
  background: #e8f7f0; /* 使用浅绿背景。 */
}

.library-message.is-error { /* 标记收藏请求失败。 */
  color: #9b3c36; /* 使用克制红色文字。 */
  background: #fff0ee; /* 使用浅红背景。 */
}

.empty-state { /* 展示无满足条件结果。 */
  padding: 3rem 1.5rem; /* 提供充足空状态留白。 */
  border: 1px dashed #c9d8e2; /* 使用虚线表达空集合。 */
  border-radius: 1rem; /* 与结果卡保持一致。 */
  text-align: center; /* 居中空状态信息。 */
  background: rgba(255, 255, 255, 0.65); /* 与页面背景区分。 */
}

.empty-state strong { /* 显示空状态主说明。 */
  color: #334e68; /* 使用正文深色。 */
  font-size: 0.92rem; /* 保持说明清晰。 */
}

.empty-state p { /* 提供调整查询建议。 */
  margin: 0.45rem 0 0; /* 与主说明分隔。 */
  color: #8293a5; /* 使用辅助文字色。 */
  font-size: 0.72rem; /* 保持建议紧凑。 */
}

.discovery-section { /* 独立展示不可合并网页发现。 */
  display: grid; /* 纵向组织说明与列表。 */
  gap: 1rem; /* 分隔标题和内容。 */
  margin-top: 1rem; /* 与论文结果拉开边界。 */
  padding: 1.4rem; /* 提供独立区块留白。 */
  border: 1px solid #e3dfd2; /* 使用暖灰边框区别学术结果。 */
  border-radius: 1rem; /* 与页面卡片协调。 */
  background: #fdfbf6; /* 使用暖白强调证据来源不同。 */
}

.discovery-section > div > p:last-child { /* 解释网页发现边界。 */
  margin: 0.45rem 0 0; /* 与标题分隔。 */
  color: #887f70; /* 使用暖灰正文色。 */
  font-size: 0.7rem; /* 保持边界说明紧凑。 */
}

.discovery-section ul { /* 纵向排列网页发现。 */
  display: grid; /* 使用网格控制间距。 */
  gap: 0.55rem; /* 分隔发现项。 */
  margin: 0; /* 移除列表默认外边距。 */
  padding: 0; /* 移除列表默认缩进。 */
  list-style: none; /* 使用卡片而非圆点。 */
}

.discovery-section li { /* 设置单个网页发现。 */
  display: grid; /* 纵向排列标题与摘要。 */
  gap: 0.25rem; /* 保持标题和摘要关联。 */
  padding: 0.75rem; /* 提供内容留白。 */
  border-radius: 0.65rem; /* 使用小圆角。 */
  background: rgba(255, 255, 255, 0.75); /* 在暖色面板中突出内容。 */
}

.discovery-section a { /* 设置网页发现链接。 */
  color: #536f7f; /* 使用区别论文标题的低饱和蓝。 */
  font-size: 0.78rem; /* 保持标题清晰。 */
  font-weight: 800; /* 提升链接可辨识性。 */
}

.discovery-section li span { /* 设置发现摘要或 URL。 */
  color: #8e887d; /* 使用暖灰辅助色。 */
  font-size: 0.66rem; /* 控制补充证据信息密度。 */
  line-height: 1.5; /* 提升摘要可读性。 */
}

@keyframes spin { /* 定义加载环旋转动画。 */
  to { transform: rotate(360deg); } /* 完成一整圈旋转。 */
}

@media (max-width: 820px) { /* 调整平板与窄屏表单。 */
  .advanced-fields { /* 将高级字段改为两列。 */
    grid-template-columns: repeat(2, minmax(0, 1fr)); /* 减少单列宽度压力。 */
  }
}

@media (max-width: 620px) { /* 调整手机搜索页布局。 */
  .search-hero { /* 缩小手机顶部留白。 */
    padding-top: 2.5rem; /* 更快进入主搜索操作。 */
  }

  .mode-row,
  .results-header { /* 将横向复杂区域改为纵向。 */
    align-items: stretch; /* 让子项填满宽度。 */
    flex-direction: column; /* 纵向排列内容。 */
  }

  .advanced-toggle { /* 将高级开关靠左。 */
    align-self: flex-start; /* 防止按钮拉伸。 */
  }

  .advanced-fields { /* 将高级条件改为单列。 */
    grid-template-columns: 1fr; /* 每行一个字段。 */
  }

  .route-summary { /* 手机来源标签左对齐。 */
    justify-content: flex-start; /* 与结果标题对齐。 */
  }

  .query-actions { /* 调整手机查询操作区。 */
    align-items: stretch; /* 让按钮高度稳定。 */
    flex-direction: column; /* 将字符计数置于按钮上方。 */
  }

  .search-button { /* 手机主按钮填满宽度。 */
    width: 100%; /* 提升触摸操作体验。 */
  }
}
</style>
