<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue' // 管理搜索表单、筛选分页和恢复轮询生命周期。

import PaperResultCard from '../components/PaperResultCard.vue' // 展示单篇证据化论文结果。
import QueryIntentPanel from '../components/QueryIntentPanel.vue' // 展示并编辑后端真实查询计划。
import SearchStats from '../components/SearchStats.vue' // 展示多源检索与排序阶段统计。
import { LibraryApiError, saveLibraryPaper } from '../services/libraryApi.js' // 将搜索结果保存到个人文献库。
import { SearchApiError, comparePapers, deleteSearchRun, getCitationGraph, getPaperDetail, getSearchRunPapers, getSearchRunUsage, getTechnicalRoutes, listSearchRuns, restoreSearchRun, streamSearchPapers, streamSearchWithIntent, translatePaperToChinese } from '../services/searchApi.js' // 使用 SSE 执行搜索、恢复运行、读取详情、翻译、比较、图谱、服务端分页、历史、用量与路线。
import { formatDuration } from '../utils/duration.js' // 将后端保存的精确毫秒耗时转换为易读单位。
import { buildDoiUrl, buildPublicPdfUrl } from '../utils/doi.js' // 将 DOI 和来源明确提供的公开 PDF 链接规范化。

const examples = [ // 提供可直接填入搜索框的复杂查询示例。
  '近五年使用大语言模型进行多变量时间序列预测，并在 ETT 数据集上实验的论文，排除综述', // 覆盖方法、任务、数据集、年份和排除条件。
  '检索视觉语言模型在医学影像报告生成中的最新研究，优先包含公开数据集', // 覆盖跨领域方法与软偏好。
  'Large language models for scientific literature retrieval and evidence-grounded recommendation', // 提供英文跨语言搜索示例。
]

const form = reactive({ // 保存搜索页当前可编辑查询条件。
  queryText: '', // 保存自然语言研究问题。
  enableSemanticRanking: false, // 默认不加载 BGE-M3，保持统一标准搜索的较短等待时间。
  enableCrossEncoderRanking: false, // 默认不加载 Cross Encoder，避免普通搜索意外变得极慢。
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
const detailPaper = ref(null) // 保存从 SQLite 读取的当前论文详情。
const detailLoading = ref(false) // 标记详情读取请求是否进行中。
const detailError = ref('') // 保存详情读取的安全公共错误。
const detailTitleTranslation = ref(null) // 保存详情抽屉单独请求的中文标题译文。
const detailAbstractTranslation = ref(null) // 保存详情抽屉单独请求的中文摘要译文。
const detailTitleTranslationLoading = ref(false) // 只标记详情标题翻译请求，不能影响摘要操作。
const detailAbstractTranslationLoading = ref(false) // 只标记详情摘要翻译请求，不能影响标题操作。
const detailTitleTranslationError = ref('') // 保存详情标题翻译的局部公共错误。
const detailAbstractTranslationError = ref('') // 保存详情摘要翻译的局部公共错误。
let detailTranslationVersion = 0 // 标记当前详情论文，阻止关闭或切换后的旧翻译响应覆盖新内容。
const comparisonPaperIds = ref([]) // 保存当前搜索结果中用户选择的二至五篇论文标识。
const comparisonResult = ref(null) // 保存后端返回的事实型固定列对比结果。
const comparisonLoading = ref(false) // 标记比较接口是否正在读取已保存论文。
const comparisonError = ref('') // 保存比较接口的安全公共错误。
const citationGraph = ref(null) // 保留受限引用图状态，当前搜索页不展示该实验性功能。
const citationGraphLoading = ref(false) // 保留引用图请求状态，避免后续恢复入口时修改数据边界。
const citationGraphError = ref('') // 保留引用图公共错误状态，不在当前页面展示。
const technicalRoutes = ref(null) // 保留技术路线状态，当前搜索页不展示该实验性功能。
const technicalRoutesLoading = ref(false) // 保留技术路线请求状态，避免后续恢复入口时修改数据边界。
const technicalRoutesError = ref('') // 保留技术路线公共错误状态，不在当前页面展示。
const searchUsage = ref(null) // 保存当前搜索运行从 SQLite 读取的实际用量快照。
const searchUsageLoading = ref(false) // 标记用量快照是否正在读取。
const searchUsageError = ref('') // 保存不泄露内部细节的用量读取错误。
const progressEvent = ref(null) // 保存最近一条不含查询正文的 SSE 进度事件。
const recoveryMessage = ref('') // 保存刷新页面后恢复运行状态的中性提示。
const resultFilters = reactive({ source: 'all', relevance: 'all', yearStart: '', yearEnd: '' }) // 保存仅作用于当前结果集合的本地筛选条件。
const resultPage = ref(1) // 保存当前结果分页页码。
const resultSort = ref('relevance') // 保存服务端支持的当前展示排序策略。
const resultPageData = ref({ items: [], total: 0, page: 1, page_size: 5, total_pages: 1 }) // 保存服务端返回的当前结果页及分页元数据。
const resultPageLoading = ref(false) // 标记已保存结果页是否正在读取。
const resultPageError = ref('') // 保存服务端筛选、排序或分页读取的安全错误。
const paperListElement = ref(null) // 保存当前页论文列表容器，用于翻页后定位到第一篇结果。
const shouldScrollToResultList = ref(false) // 仅记录用户主动翻页后的定位需求，筛选刷新不触发滚动。
const searchHistory = ref([]) // 保存不含查询正文和论文内容的本地运行索引。
const searchHistoryLoading = ref(false) // 标记运行历史是否正在读取。
const searchHistoryError = ref('') // 保存历史读取或清理的安全错误。
const searchHistoryExpanded = ref(false) // 控制历史面板展开状态，恢复成功后自动收起。
const currentRunId = ref(new URLSearchParams(globalThis.location?.search || '').get('run_id')?.trim() || '') // 保存地址中的当前运行标识以区分首页与结果页。
const deletingRunId = ref('') // 标记当前经用户确认正在清理的终态运行。
const RESULT_PAGE_SIZE = 5 // 限制单页论文卡片数量，避免结果较多时页面过长。
const RECOVERY_POLL_INTERVAL_MS = 3000 // 使用短周期只读轮询作为刷新后无法重连 POST SSE 的回退。
const RECOVERY_POLL_MAX_ATTEMPTS = 20 // 最多轮询一分钟，避免异常状态无限占用浏览器和后端资源。
let recoveryPollTimer = null // 保存当前待执行轮询定时器，便于新搜索和卸载时取消。
let recoveryRunId = '' // 保存当前允许轮询的运行标识，防止旧响应覆盖新搜索。
let recoveryPollAttempts = 0 // 记录当前恢复运行的已执行轮询次数。
let resultPageRequestVersion = 0 // 标记最新分页请求，防止快速切换条件时旧响应覆盖新页面。

const routeSources = computed(() => result.value?.run_state?.selected_sources || result.value?.route_plan?.academic_sources || []) // 优先提取多轮实际参与的学术来源并兼容旧响应。
const conditionChips = ref([]) // 保存最近一次成功提交的条件标签，避免后续编辑表单改变旧结果说明。
const showSearchHistory = computed(() => !currentRunId.value) // 仅在不带运行标识的首页展示已保存搜索运行。

const discoveries = computed(() => result.value?.discoveries || []) // 保持补充网页发现与论文结果独立展示。
const runState = computed(() => result.value?.run_state || null) // 提取多轮运行状态供搜索页展示过程和停止原因。
const coverageReport = computed(() => result.value?.coverage_report || runState.value?.coverage_report || null) // 提取累计候选覆盖报告并兼容后续状态存储。
const planningMeta = computed(() => ({ // 将后端查询规划观测字段映射为面板属性。
  modelName: result.value?.query_planning_model_name || null, // 自然入口展示实际模型，直接重搜时为空。
  promptTokens: result.value?.query_planning_prompt_tokens || 0, // 展示规划输入 Token。
  completionTokens: result.value?.query_planning_completion_tokens || 0, // 展示规划输出 Token。
  durationMs: result.value?.query_planning_duration_ms || 0, // 展示规划耗时。
}))
const availableResultSources = computed(() => [...new Set((result.value?.papers || []).map((paper) => paper.source).filter(Boolean))]) // 基于同次最终结果生成可选来源，避免写死供应商名称。
const paperPagination = computed(() => resultPageData.value) // 仅消费服务端从同次 SQLite 快照返回的当前结果页。
const selectedComparisonPapers = computed(() => (result.value?.papers || []).filter((paper) => comparisonPaperIds.value.includes(paper.paper_id))) // 始终从当前同次最终结果恢复比较选择，不信任前端副本。
const detailDoiUrl = computed(() => buildDoiUrl(detailPaper.value?.doi)) // 只为详情中的合法 DOI 渲染固定 doi.org 新标签链接。
const detailPublicPdfUrl = computed(() => buildPublicPdfUrl(detailPaper.value?.open_access_url)) // 只为详情中的来源明确 PDF 渲染独立公开访问入口。
const citationGraphLayout = computed(() => { // 为保留的受限图能力计算确定性圆形布局。
  const nodes = citationGraph.value?.nodes || [] // 获取后端已裁剪的节点集合。
  const radius = Math.max(95, Math.min(150, nodes.length * 14)) // 按节点数量限定圆形半径以减少重叠。
  return nodes.map((node, index) => ({ ...node, x: 210 + radius * Math.cos((Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2), y: 190 + radius * Math.sin((Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2) })) // 返回 SVG 视图坐标。
})
const citationGraphNodeMap = computed(() => new Map(citationGraphLayout.value.map((node) => [node.paper_id, node]))) // 供保留图谱能力快速查找两端节点坐标。

watch(() => [resultFilters.source, resultFilters.relevance, resultFilters.yearStart, resultFilters.yearEnd, resultSort.value], () => { // 任意筛选或排序变化时回到第一页避免越界空页。
  resultPage.value = 1 // 保持筛选后的首屏结果可见。
  void loadSearchResultPage() // 由服务端基于已保存结果执行筛选、排序与分页。
})

watch(() => resultPage.value, () => { // 用户切换页码时只读取同次已保存结果，不重新检索。
  void loadSearchResultPage() // 将当前页码作为服务端分页参数。
})

watch(() => result.value?.run_state?.run_id, () => { // 新搜索或恢复到另一运行时重置旧筛选和页码。
  resultFilters.source = 'all' // 恢复来源不过滤。
  resultFilters.relevance = 'all' // 恢复核验状态不过滤。
  resultFilters.yearStart = '' // 清除旧年份起点。
  resultFilters.yearEnd = '' // 清除旧年份终点。
  resultPage.value = 1 // 回到结果第一页。
  comparisonPaperIds.value = [] // 新运行结果不能复用旧运行的论文选择。
  comparisonResult.value = null // 清除旧结果可能对应的比较列。
  comparisonError.value = '' // 清除旧运行比较错误。
  citationGraph.value = null // 清除旧运行的关系图数据。
  citationGraphError.value = '' // 清除旧运行图谱错误。
  technicalRoutes.value = null // 清除旧运行路线集合。
  technicalRoutesError.value = '' // 清除旧运行路线错误。
  searchUsage.value = null // 防止旧运行统计混入新搜索结果。
  searchUsageLoading.value = false // 新运行开始前取消旧请求遗留的加载提示。
  searchUsageError.value = '' // 清除旧运行用量读取错误。
  resultPageData.value = { items: [], total: 0, page: 1, page_size: RESULT_PAGE_SIZE, total_pages: 1 } // 清除旧运行页面，避免论文卡片短暂错配。
  resultPageError.value = '' // 清除旧运行分页读取错误。
  shouldScrollToResultList.value = false // 新搜索或恢复运行不能继承旧分页操作的滚动请求。
  if (result.value?.run_state?.run_id) void loadSearchResultPage(result.value.run_state.run_id) // 新运行结果到达后立即读取服务端首个结果页。
})

function useExample(example) { // 将示例查询填入输入框但不自动发起外部调用。
  form.queryText = example // 允许用户继续编辑示例。
  errorMessage.value = '' // 清除此前表单错误。
}

function buildConditionChipsFromIntent(intent) { // 将已保存 QueryIntent 转换为恢复结果所需的条件标签。
  return buildConditionChips({ // 复用与新提交结果一致的标签规则。
    enableSemanticRanking: Boolean(intent.enable_semantic_ranking), // 回显本次是否实际使用 BGE-M3。
    enableCrossEncoderRanking: Boolean(intent.enable_cross_encoder_ranking), // 回显本次是否实际使用 Cross Encoder。
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
  currentRunId.value = runId // 同步响应式页面状态，使结果页立即隐藏历史入口。
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
    void loadSearchUsage(state.run_id) // 异步读取同次快照，不阻塞已恢复结果的首屏渲染。
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
  const chips = [{ label: '标准检索', tone: 'neutral' }] // 搜索页统一使用两轮标准检索策略。
  if (formSnapshot.enableSemanticRanking) chips.push({ label: 'BGE-M3 粗排', tone: 'neutral' }) // 标识本次实际启用的本地语义模型。
  if (formSnapshot.enableCrossEncoderRanking) chips.push({ label: 'Cross Encoder 重排', tone: 'neutral' }) // 标识本次实际启用的本地精排模型。
  if (formSnapshot.startYear && formSnapshot.endYear) chips.push({ label: `${formSnapshot.startYear}–${formSnapshot.endYear}`, tone: 'neutral' }) // 展示年份范围。
  if (formSnapshot.mustInclude) chips.push({ label: `必须：${formSnapshot.mustInclude}`, tone: 'positive' }) // 展示硬约束。
  if (formSnapshot.shouldInclude) chips.push({ label: `优先：${formSnapshot.shouldInclude}`, tone: 'neutral' }) // 展示软偏好。
  if (formSnapshot.exclude) chips.push({ label: `排除：${formSnapshot.exclude}`, tone: 'negative' }) // 展示排除条件。
  return chips // 返回保持表单顺序的标签列表。
}

function changeResultPage(nextPage) { // 切换筛选后结果页，并限制在当前总页数内。
  const normalizedPage = Math.min(Math.max(nextPage, 1), paperPagination.value.total_pages) // 防止筛选变化或按钮连点导致越界。
  if (normalizedPage === resultPage.value) return // 首尾页点击无效时不产生额外请求或滚动。
  shouldScrollToResultList.value = true // 只在用户实际翻页时请求定位到新页首篇论文。
  resultPage.value = normalizedPage // 交由现有页码监听器读取对应的已保存结果。
}

function scrollToCurrentPageFirstPaper() { // 将新页第一篇论文定位到固定顶栏下方，隐藏前置的论文比较区。
  const paperList = paperListElement.value // 读取已经渲染完成的当前页论文列表容器。
  if (!paperList || !globalThis.scrollTo) return // 非浏览器环境或列表为空时安全跳过定位。
  const topbarHeight = globalThis.document?.querySelector('.topbar')?.getBoundingClientRect().height || 0 // 读取响应式固定顶栏的实际高度，避免遮住首篇论文。
  const listTop = paperList.getBoundingClientRect().top + (globalThis.scrollY || 0) // 将视口坐标换算为文档中的稳定纵向位置。
  globalThis.scrollTo({ top: Math.max(0, listTop - topbarHeight), behavior: 'smooth' }) // 让比较工具栏恰好滚到顶栏之上，并完整显示第一篇论文。
}

function formatHistoryTime(value) { // 将服务端 UTC 时间转换为浏览器本地可读的紧凑时间文本。
  const date = new Date(value) // 解析后端序列化的 ISO 时间。
  return Number.isNaN(date.getTime()) ? '时间暂缺' : date.toLocaleString() // 历史 JSON 异常时不抛出页面渲染错误。
}

function clearRunIdFromUrl(runId) { // 删除当前运行后移除地址中的失效恢复标识。
  if (!globalThis.location?.href || !globalThis.history?.replaceState) return // 非浏览器环境保持函数安全无副作用。
  const url = new URL(globalThis.location.href) // 从当前页面地址构造可安全修改的 URL。
  if (url.searchParams.get('run_id') !== runId) return // 仅清除与被删除记录完全一致的标识。
  url.searchParams.delete('run_id') // 删除失效运行标识避免刷新后继续恢复。
  globalThis.history.replaceState(null, '', url) // 使用替换避免额外污染浏览历史。
  currentRunId.value = '' // 回到首页状态后允许重新展示历史入口。
}

async function loadSearchHistory() { // 读取有限本地运行索引，不加载查询正文、论文或外部来源。
  if (!showSearchHistory.value) return // 当前结果页不展示历史，也不发起无用的索引读取。
  searchHistoryLoading.value = true // 展示历史面板的读取中状态。
  searchHistoryError.value = '' // 清除旧的读取或清理错误。
  try { // 通过客户端公共边界读取最近运行。
    const history = await listSearchRuns(10) // 固定读取最近十条，避免列表无限增长。
    searchHistory.value = history.items // 仅保存后端允许展示的最小索引字段。
  } catch (error) { // 将客户端已净化错误映射为折叠面板提示。
    searchHistoryError.value = error instanceof SearchApiError ? error.message : '读取搜索运行历史时出现未知错误，请稍后重试' // 不展示存储或网络内部细节。
  } finally { // 无论成功失败都结束历史加载状态。
    searchHistoryLoading.value = false // 恢复历史面板操作。
  }
}

async function restoreSearchHistoryRun(runId) { // 从用户选择的历史索引恢复同次运行，不重新执行检索。
  if (loading.value) return // 当前搜索或恢复进行中时避免并发覆盖页面状态。
  stopRecoveryPolling() // 停止旧运行的恢复轮询，防止其覆盖新选择。
  loading.value = true // 在历史恢复期间禁用新搜索提交。
  errorMessage.value = '' // 清除旧搜索或恢复错误。
  recoveryMessage.value = '' // 清除旧恢复提示。
  try { // 只使用既有 REST 恢复边界。
    const recovered = await restoreSearchRun(runId) // 读取轻量状态，终态再读取同次完整结果。
    const hasResult = applyRecoveredRun(recovered) // 复用统一恢复映射并保持结果来源一致。
    syncRunIdToUrl(recovered.state.run_id) // 让刷新后的页面继续关联用户选择的历史运行。
    if (hasResult) searchHistoryExpanded.value = false // 仅在结果确实恢复后收起历史面板，失败时保留现场。
    if (shouldPollRecoveredRun(recovered.state, hasResult)) startRecoveryPolling(recovered.state.run_id) // 尚未形成最终结果时继续有限只读轮询。
  } catch (error) { // 将历史条目过期或读取失败转换为安全页面错误。
    errorMessage.value = error instanceof SearchApiError ? error.message : '恢复历史搜索运行时出现未知错误，请稍后重试' // 不展示底层存储细节。
  } finally { // 所有恢复分支都恢复页面交互。
    loading.value = false // 结束恢复加载状态。
  }
}

async function removeSearchHistoryRun(run) { // 在用户确认后清理一条终态本地运行及同次完整结果。
  if (deletingRunId.value || !run?.run_id) return // 防止并发删除或损坏历史索引进入删除边界。
  if (globalThis.confirm && !globalThis.confirm('将永久清理该搜索运行及同次结果，是否继续？')) return // 浏览器确认取消时保持本地快照不变。
  deletingRunId.value = run.run_id // 立即标记当前条目，避免重复点击造成重复 DELETE。
  searchHistoryError.value = '' // 清除旧删除错误。
  try { // 由后端校验终态并原子删除两类快照。
    await deleteSearchRun(run.run_id) // 不在前端假设删除成功或直接操作本地 SQLite。
    searchHistory.value = searchHistory.value.filter((item) => item.run_id !== run.run_id) // 成功后仅移除当前索引条目。
    if (runState.value?.run_id === run.run_id) { // 当前正在展示被清理运行时必须清除失效结果。
      result.value = null // 不继续展示已经不存在的结果快照。
      progressEvent.value = null // 清除与已删除运行关联的进度提示。
      recoveryMessage.value = '当前搜索运行已清理' // 明确告知页面不再可恢复该结果。
      clearRunIdFromUrl(run.run_id) // 防止刷新后使用失效运行标识再次恢复。
    }
  } catch (error) { // 将运行中 409、过期 404 或服务故障映射为公共提示。
    searchHistoryError.value = error instanceof SearchApiError ? error.message : '清理搜索运行时出现未知错误，请稍后重试' // 不展示持久化堆栈或路径。
  } finally { // 无论成功失败均解除当前条目操作锁。
    deletingRunId.value = '' // 允许用户继续处理其他历史条目或重试。
  }
}

async function loadSearchResultPage(runId = runState.value?.run_id) { // 从服务端读取当前筛选、排序和页码对应的已保存结果页。
  const normalizedRunId = String(runId || '').trim() // 规范化当前完整结果关联的稳定运行标识。
  if (!normalizedRunId) return // 尚未获得完成结果或运行标识时不请求资源。
  const requestVersion = ++resultPageRequestVersion // 记录本次请求版本以丢弃快速筛选时的迟到响应。
  resultPageLoading.value = true // 明确展示页面正在刷新已保存结果。
  resultPageError.value = '' // 清除当前请求前的安全错误。
  try { // 不传递论文事实，只传递公开筛选、排序和分页参数。
    const nextPage = await getSearchRunPapers(normalizedRunId, { // 读取同次 SQLite 最终结果的当前页。
      source: resultFilters.source,
      relevance: resultFilters.relevance,
      yearStart: resultFilters.yearStart,
      yearEnd: resultFilters.yearEnd,
      sort: resultSort.value,
      page: resultPage.value,
      pageSize: RESULT_PAGE_SIZE,
    })
    if (requestVersion === resultPageRequestVersion && runState.value?.run_id === normalizedRunId) { // 仅接受当前运行且最新条件对应的响应。
      resultPageData.value = nextPage // 替换为后端已校正页码和统计的唯一事实源。
      resultPage.value = nextPage.page // 服务端在筛选收缩页数时可安全校正越界页码。
      if (shouldScrollToResultList.value) { // 只处理用户点击分页控件发起的页面切换。
        shouldScrollToResultList.value = false // 先消费标记，避免后续筛选或响应重复滚动。
        await nextTick() // 等待 Vue 将新页第一篇论文真实渲染到列表容器中。
        if (requestVersion === resultPageRequestVersion) scrollToCurrentPageFirstPaper() // 基于固定顶栏实际高度完整显示新页第一篇论文。
      }
    }
  } catch (error) { // 将客户端已净化错误映射为紧凑页面提示。
    if (requestVersion === resultPageRequestVersion && runState.value?.run_id === normalizedRunId) { // 只显示当前运行和最新条件的失败。
      shouldScrollToResultList.value = false // 当前翻页读取失败时取消定位，避免后续请求误触发滚动。
      resultPageError.value = error instanceof SearchApiError ? error.message : '读取筛选后的搜索结果时出现未知错误，请稍后重试' // 不展示网络或存储内部细节。
    }
  } finally { // 仅由最新请求关闭加载状态，避免旧请求误导当前筛选界面。
    if (requestVersion === resultPageRequestVersion && runState.value?.run_id === normalizedRunId) resultPageLoading.value = false // 恢复分页控件可操作状态。
  }
}

async function loadSearchUsage(runId) { // 读取当前结果对应的持久化用量，不发起新的检索或来源调用。
  const normalizedRunId = String(runId || '').trim() // 规范化结果或恢复状态提供的运行标识。
  if (!normalizedRunId) return // 尚未收到首个 SSE 运行标识时无需读取资源。
  searchUsageLoading.value = true // 让页面明确区分正在读取与零用量。
  searchUsageError.value = '' // 清除同一运行的旧读取错误。
  try { // 仅消费后端返回的同次 SQLite 快照。
    const usage = await getSearchRunUsage(normalizedRunId) // 通过可替换 API 客户端读取实际统计。
    if (runState.value?.run_id === normalizedRunId) searchUsage.value = usage // 忽略新搜索开始后迟到的旧运行响应。
  } catch (error) { // 将已净化的客户端错误映射为页面提示。
    if (runState.value?.run_id === normalizedRunId) searchUsageError.value = error instanceof SearchApiError ? error.message : '读取搜索用量时出现未知错误，请稍后重试' // 只展示安全公共消息。
  } finally { // 无论成功或失败均释放当前运行的加载状态。
    if (runState.value?.run_id === normalizedRunId) searchUsageLoading.value = false // 防止旧请求覆盖新运行的加载提示。
  }
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
    syncRunIdToUrl(nextResult.run_state?.run_id) // 终态响应也同步运行标识，兼容缺失 SSE 创建事件的情况。
    void loadSearchUsage(nextResult.run_state?.run_id) // 读取已持久化的实际统计，不等待而延迟展示结果。
    submittedQuery.value = formSnapshot.queryText.trim() // 保存结果对应查询供标题回显。
    conditionChips.value = buildConditionChips(formSnapshot) // 保存与当前结果严格对应的条件标签。
    recoveryMessage.value = '' // 新搜索成功后清除旧运行恢复提示。
    void loadSearchHistory() // 搜索完成后刷新本地运行索引以提供恢复和清理入口。
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
    syncRunIdToUrl(nextResult.run_state?.run_id) // 确保编辑重搜完成后立即进入结果页状态。
    void loadSearchUsage(nextResult.run_state?.run_id) // 读取编辑重搜对应的同次实际用量快照。
    submittedQuery.value = editedIntent.original_query // 保持结果对应的原始研究问题。
    conditionChips.value = buildConditionChips({ // 使用编辑后的关键约束更新结果说明。
      enableSemanticRanking: Boolean(editedIntent.enable_semantic_ranking), // 映射编辑重搜的 BGE-M3 选择。
      enableCrossEncoderRanking: Boolean(editedIntent.enable_cross_encoder_ranking), // 映射编辑重搜的 Cross Encoder 选择。
      startYear: editedIntent.year_range?.[0] || '', // 映射起始年份。
      endYear: editedIntent.year_range?.[1] || '', // 映射结束年份。
      mustInclude: (editedIntent.must_include || []).join(', '), // 映射硬约束。
      shouldInclude: (editedIntent.should_include || []).join(', '), // 映射软偏好。
      exclude: (editedIntent.exclude || []).join(', '), // 映射排除条件。
    })
    recoveryMessage.value = '' // 编辑重搜成功后清除旧运行恢复提示。
    void loadSearchHistory() // 编辑重搜完成后同步刷新历史索引。
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
  if (showSearchHistory.value) void loadSearchHistory() // 仅首页读取有限历史索引，不阻塞新搜索输入。
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

async function openPaperDetail(paper) { // 按用户点击只读读取详情，避免卡片渲染时批量请求。
  detailPaper.value = null // 清除上一条详情，防止旧内容在加载期间误导用户。
  detailError.value = '' // 清除上一条详情错误。
  resetDetailTranslations() // 切换论文时清空上一条论文的字段级译文状态。
  detailLoading.value = true // 展示详情抽屉的读取中状态。
  try { // 统一处理 API 客户端的公共错误。
    detailPaper.value = await getPaperDetail(paper.paper_id) // 仅读取 SQLite 保存的规范化论文记录。
  } catch (error) { // 不展示底层网络或持久化细节。
    detailError.value = error instanceof SearchApiError ? error.message : '读取论文详情时出现未知错误，请稍后重试' // 提供可安全展示的失败说明。
  } finally { // 无论成功失败都结束加载状态。
    detailLoading.value = false // 恢复抽屉中的操作状态。
  }
}

function closePaperDetail() { // 关闭详情抽屉并释放当前展示数据。
  detailPaper.value = null // 不在页面内长期保留论文详情副本。
  detailError.value = '' // 清除可能存在的错误提示。
  detailLoading.value = false // 防御关闭时遗留的加载状态。
  resetDetailTranslations() // 关闭时释放标题和摘要的本次页面状态。
}

function resetDetailTranslations() { // 清空详情抽屉两种字段翻译的显示、加载和错误状态。
  detailTranslationVersion += 1 // 让正在返回的旧论文翻译响应失效。
  detailTitleTranslation.value = null // 防止新论文显示旧论文的中文标题。
  detailAbstractTranslation.value = null // 防止新论文显示旧论文的中文摘要。
  detailTitleTranslationLoading.value = false // 防御关闭或切换时遗留标题加载状态。
  detailAbstractTranslationLoading.value = false // 防御关闭或切换时遗留摘要加载状态。
  detailTitleTranslationError.value = '' // 清除标题字段的旧错误。
  detailAbstractTranslationError.value = '' // 清除摘要字段的旧错误。
}

async function translateDetailField(field) { // 在详情抽屉中独立请求指定字段的已缓存中文翻译。
  if (!detailPaper.value) return // 详情尚未读取完成时不得构造翻译请求。
  const paperId = detailPaper.value.paper_id // 固定本次请求对应的论文，防止切换详情后写入新论文。
  const requestVersion = detailTranslationVersion // 记录当前详情版本以识别关闭或切换后的过期响应。
  const translation = field === 'title' ? detailTitleTranslation : detailAbstractTranslation // 选择当前字段的展示结果状态。
  const loading = field === 'title' ? detailTitleTranslationLoading : detailAbstractTranslationLoading // 选择当前字段的加载状态。
  const errorState = field === 'title' ? detailTitleTranslationError : detailAbstractTranslationError // 选择当前字段的错误状态。
  if (translation.value || loading.value) return // 已翻译或当前字段请求中时不重复调用。
  loading.value = true // 只让当前字段按钮显示正在翻译。
  errorState.value = '' // 清除当前字段用户重试前的错误。
  try { // 复用后端的 SQLite 缓存和 DeepSeek 受控调用边界。
    const translated = await translatePaperToChinese(paperId, field) // 只提交本次已保存论文标识和当前字段名。
    if (requestVersion !== detailTranslationVersion || detailPaper.value?.paper_id !== paperId) return // 关闭或切换详情后丢弃旧响应。
    translation.value = translated // 只写入当前详情论文和当前字段的译文。
  } catch (error) { // 将客户端公共错误显示在当前字段附近。
    if (requestVersion !== detailTranslationVersion || detailPaper.value?.paper_id !== paperId) return // 关闭或切换详情后不显示旧请求错误。
    errorState.value = error instanceof SearchApiError ? error.message : '论文翻译暂时不可用，请稍后重试' // 不展示网络或服务端内部细节。
  } finally { // 无论成功或失败都恢复该字段重试能力。
    if (requestVersion === detailTranslationVersion && detailPaper.value?.paper_id === paperId) loading.value = false // 只结束当前详情论文和字段的加载状态。
  }
}

function togglePaperComparison(paper) { // 将当前论文加入或移出最多五篇的比较集合。
  const index = comparisonPaperIds.value.indexOf(paper.paper_id) // 查找当前论文是否已经被选择。
  if (index >= 0) { // 已选择时允许用户取消。
    comparisonPaperIds.value.splice(index, 1) // 原地移除以保持 Vue 响应式更新。
    comparisonResult.value = null // 选择改变后旧比较列不再可信。
    comparisonError.value = '' // 清除旧比较失败提示。
    return // 不继续执行新增上限判断。
  }
  if (comparisonPaperIds.value.length >= 5) { // 固定比较列最多五篇，避免界面和语义边界扩张。
    comparisonError.value = '一次最多比较 5 篇论文' // 提供明确可操作的前端提示。
    return // 阻止第六篇进入无效状态。
  }
  comparisonPaperIds.value.push(paper.paper_id) // 保持用户点击顺序作为后端和前端列顺序。
  comparisonResult.value = null // 新增论文后必须重新读取可信对比事实。
  comparisonError.value = '' // 清除可能存在的旧错误。
}

function clearPaperComparison() { // 清空当前小集合选择并关闭已生成的比较结果。
  comparisonPaperIds.value = [] // 移除所有已选择标识。
  comparisonResult.value = null // 关闭对比弹层。
  comparisonError.value = '' // 清除提示。
}

async function openPaperComparison() { // 请求后端按选择顺序读取 SQLite 事实并生成固定列。
  if (comparisonPaperIds.value.length < 2) { // 两篇以下不具备比较意义。
    comparisonError.value = '请至少选择 2 篇论文进行比较' // 指引用户完成最小选择。
    return // 不发起无效请求。
  }
  comparisonLoading.value = true // 显示比较读取中状态。
  comparisonError.value = '' // 清除上次失败信息。
  try { // 将客户端公共错误映射为页面提示。
    comparisonResult.value = await comparePapers(comparisonPaperIds.value) // 仅读取已保存论文事实，不调用外部来源。
  } catch (error) { // 不展示持久化或网络底层细节。
    comparisonError.value = error instanceof SearchApiError ? error.message : '读取论文比较结果时出现未知错误，请稍后重试' // 保持用户可理解的失败边界。
  } finally { // 无论成功失败都结束加载状态。
    comparisonLoading.value = false // 恢复比较操作按钮。
  }
}

function closePaperComparison() { // 关闭比较弹层而保留选择，方便用户调整后再次比较。
  comparisonResult.value = null // 关闭事实型固定列展示。
  comparisonError.value = '' // 清除抽层错误信息。
  comparisonLoading.value = false // 防御关闭时遗留加载状态。
}

async function openCitationGraph() { // 读取当前最终结果内可验证的引用和版本族关系。
  const paperIds = (result.value?.papers || []).map((paper) => paper.paper_id).filter(Boolean) // 只提交当前同次搜索的稳定论文标识。
  if (!paperIds.length) { // 无搜索结果时不能生成空图。
    citationGraphError.value = '当前没有可用于生成引用图的论文' // 提供明确空状态提示。
    return // 不发起无效请求。
  }
  citationGraphLoading.value = true // 打开图谱读取中弹层。
  citationGraphError.value = '' // 清除旧图谱失败提示。
  try { // 将客户端公共错误映射为页面提示。
    citationGraph.value = await getCitationGraph(paperIds, undefined, undefined, 30) // 只读取 SQLite 已保存的最多 30 个节点。
  } catch (error) { // 不展示底层网络或持久化细节。
    citationGraphError.value = error instanceof SearchApiError ? error.message : '读取引用图时出现未知错误，请稍后重试' // 保持可安全展示的错误边界。
  } finally { // 无论成功失败都结束读取状态。
    citationGraphLoading.value = false // 恢复图谱操作按钮。
  }
}

function closeCitationGraph() { // 关闭引用图弹层并释放本次布局数据。
  citationGraph.value = null // 不在页面内长期保留关系图副本。
  citationGraphError.value = '' // 清除图谱错误提示。
  citationGraphLoading.value = false // 防御关闭时遗留的加载状态。
}

function openCitationGraphPaper(node) { // 点击图节点后复用已有详情读取入口。
  closeCitationGraph() // 先关闭关系图避免两个弹层叠加。
  void openPaperDetail(node) // 使用节点中的稳定标识只读读取完整论文详情。
}

async function openTechnicalRoutes() { // 从当前已保存论文关键词读取保守路线。
  const paperIds = (result.value?.papers || []).map((paper) => paper.paper_id).filter(Boolean) // 只提交本次搜索稳定论文标识。
  technicalRoutesLoading.value = true // 展示路线读取中状态。
  technicalRoutesError.value = '' // 清除旧错误。
  try { technicalRoutes.value = await getTechnicalRoutes(paperIds) } catch (error) { technicalRoutesError.value = error instanceof SearchApiError ? error.message : '读取技术路线时出现未知错误，请稍后重试' } finally { technicalRoutesLoading.value = false } // 保持安全错误边界并恢复按钮。
}

function closeTechnicalRoutes() { // 关闭路线弹层并释放当前结果。
  technicalRoutes.value = null // 不长期保留路线副本。
  technicalRoutesError.value = '' // 清除路线错误。
  technicalRoutesLoading.value = false // 防御关闭时遗留加载状态。
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
        <div class="ranking-row">
          <div class="ranking-options" role="group" aria-label="本地排序选项">
            <label><input v-model="form.enableSemanticRanking" type="checkbox" :disabled="loading">启用 BGE-M3 语义粗排</label>
            <label><input v-model="form.enableCrossEncoderRanking" type="checkbox" :disabled="loading">启用 Cross Encoder 重排</label>
            <p>标准检索最多执行 2 轮。两项均默认关闭；开启任一项会加载本地模型，搜索时长可能变得极长。</p>
          </div>
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
      <details v-if="showSearchHistory" class="search-history" :open="searchHistoryExpanded || searchHistoryLoading || Boolean(searchHistoryError)" @toggle="searchHistoryExpanded = $event.currentTarget.open">
        <summary>已保存的搜索运行 <span>{{ searchHistory.length }}</span></summary>
        <p>仅显示本地运行状态与时间，不展示查询正文或论文内容。</p>
        <p v-if="searchHistoryLoading" class="history-message">正在读取运行历史…</p>
        <p v-else-if="searchHistoryError" class="history-message is-error" role="alert">{{ searchHistoryError }}</p>
        <ul v-else-if="searchHistory.length">
          <li v-for="item in searchHistory" :key="item.run_id">
            <div><strong>{{ item.status }}</strong><span>{{ `${item.current_round} / ${item.max_rounds} 轮 · ${formatHistoryTime(item.updated_at)}` }}</span><small>{{ item.stop_reason || (item.result_ready ? '结果已保存' : '结果尚未就绪') }}</small></div>
            <div class="history-actions"><button type="button" :disabled="loading" @click="restoreSearchHistoryRun(item.run_id)">{{ item.result_ready ? '恢复结果' : '查看状态' }}</button><button type="button" class="history-delete" :disabled="deletingRunId === item.run_id || !['completed', 'failed', 'cancelled'].includes(item.status)" @click="removeSearchHistoryRun(item)">{{ deletingRunId === item.run_id ? '正在清理…' : '清理' }}</button></div>
          </li>
        </ul>
        <p v-else class="history-message">暂无可恢复的本地搜索运行。</p>
      </details>
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
        <div class="search-usage" aria-label="本次搜索实际用量">
          <strong>本次实际用量</strong>
          <span v-if="searchUsageLoading">正在读取已保存统计…</span>
          <span v-else-if="searchUsageError" role="alert">{{ searchUsageError }}</span>
          <dl v-else-if="searchUsage">
            <div><dt>API</dt><dd>{{ searchUsage.api_call_count }} 次</dd></div>
            <div><dt>Token</dt><dd>{{ searchUsage.token_usage }}</dd></div>
            <div><dt>总耗时</dt><dd>{{ formatDuration(searchUsage.latency_ms) }}</dd></div>
            <div><dt>缓存命中</dt><dd>{{ `${searchUsage.cache_hits} 次` }}</dd></div>
          </dl>
        </div>
      </section>
      <QueryIntentPanel v-if="result.query_intent" :intent="result.query_intent" :planning-meta="planningMeta" :disabled="loading" @resubmit="resubmitIntent" />
      <header class="results-header">
        <div>
          <p class="eyebrow">EVIDENCE-GROUNDED RESULTS</p>
          <h2 id="results-title">最终推荐 <span>{{ paperPagination.total }} / {{ result.papers.length }}</span></h2>
          <p class="submitted-query">“{{ submittedQuery }}”</p>
        </div>
        <div class="route-summary" aria-label="本次检索来源">
          <span v-for="source in routeSources" :key="source">{{ source }}</span>
        </div>
      </header>
      <div class="condition-row" aria-label="当前检索条件">
        <span v-for="chip in conditionChips" :key="chip.label" :class="['condition-chip', `is-${chip.tone}`]">{{ chip.label }}</span>
      </div>
      <section v-if="result.papers.length" class="result-controls" aria-label="搜索结果筛选">
        <div class="filter-fields">
          <label>来源<select v-model="resultFilters.source" :disabled="resultPageLoading"><option value="all">全部来源</option><option v-for="source in availableResultSources" :key="source" :value="source">{{ source }}</option></select></label>
          <label>核验<select v-model="resultFilters.relevance" :disabled="resultPageLoading"><option value="all">全部状态</option><option value="satisfied">已满足</option><option value="uncertain">待确认</option><option value="not_satisfied">未满足</option></select></label>
          <label>起始年<input v-model="resultFilters.yearStart" type="number" min="1800" max="2100" placeholder="不限" :disabled="resultPageLoading"></label>
          <label>结束年<input v-model="resultFilters.yearEnd" type="number" min="1800" max="2100" placeholder="不限" :disabled="resultPageLoading"></label>
          <label>排序<select v-model="resultSort" :disabled="resultPageLoading"><option value="relevance">相关性</option><option value="year_desc">最新发表</option><option value="citation_desc">引用量</option></select></label>
        </div>
        <p>{{ resultPageLoading ? '正在读取已保存的筛选结果…' : `筛选后 ${paperPagination.total} 篇，第 ${paperPagination.page} / ${paperPagination.total_pages} 页` }}</p>
      </section>
      <p v-if="resultPageError" class="comparison-message" role="alert">{{ resultPageError }}</p>
      <section class="comparison-toolbar" aria-label="论文比较选择">
        <div><strong>论文比较</strong><span>{{ `已选择 ${comparisonPaperIds.length} / 5 篇` }}</span></div>
        <div><button type="button" :disabled="comparisonPaperIds.length < 2 || comparisonLoading" @click="openPaperComparison">{{ comparisonLoading ? '正在整理比较…' : `比较 ${comparisonPaperIds.length} 篇` }}</button><button v-if="comparisonPaperIds.length" type="button" class="comparison-clear" @click="clearPaperComparison">清空</button></div>
      </section>
      <p v-if="comparisonError && !comparisonResult" class="comparison-message" role="alert">{{ comparisonError }}</p>
      <p v-if="libraryMessage.text" :class="['library-message', `is-${libraryMessage.tone}`]" role="status">{{ libraryMessage.text }}</p>
      <div v-if="paperPagination.items.length" ref="paperListElement" class="paper-list">
        <PaperResultCard v-for="(paper, index) in paperPagination.items" :key="paper.paper_id" :paper="paper" :rank="(paperPagination.page - 1) * paperPagination.page_size + index + 1" :saved="savedPaperIds.has(paper.paper_id)" :saving="savingPaperIds.has(paper.paper_id)" :comparison-selected="comparisonPaperIds.includes(paper.paper_id)" :comparison-disabled="comparisonPaperIds.length >= 5" @save="savePaper" @detail="openPaperDetail" @compare="togglePaperComparison" />
      </div>
      <div v-else class="empty-state">
        <strong>{{ result.papers.length ? '没有论文符合当前筛选条件' : '暂未找到满足全部条件的论文' }}</strong>
        <p>{{ result.papers.length ? '可以调整来源、年份或核验状态筛选。' : '可以放宽年份、必须词或排除条件后重新检索。' }}</p>
      </div>
      <nav v-if="paperPagination.total_pages > 1" class="result-pagination" aria-label="搜索结果分页">
        <button type="button" :disabled="resultPageLoading || paperPagination.page === 1" @click="changeResultPage(paperPagination.page - 1)">上一页</button>
        <span>{{ `${paperPagination.page} / ${paperPagination.total_pages}` }}</span>
        <button type="button" :disabled="resultPageLoading || paperPagination.page === paperPagination.total_pages" @click="changeResultPage(paperPagination.page + 1)">下一页</button>
      </nav>
      <div v-if="detailPaper || detailLoading || detailError" class="paper-detail-backdrop" @click.self="closePaperDetail">
        <aside class="paper-detail-panel" role="dialog" aria-modal="true" aria-labelledby="paper-detail-title">
          <button class="detail-close" type="button" aria-label="关闭论文详情" @click="closePaperDetail">×</button>
          <p class="eyebrow">SAVED PAPER DETAIL</p>
          <p v-if="detailLoading" class="detail-status">正在读取已保存的论文详情…</p>
          <p v-else-if="detailError" class="detail-error" role="alert">{{ detailError }}</p>
          <template v-else-if="detailPaper">
            <h2 id="paper-detail-title">{{ detailPaper.title }}</h2>
            <div class="detail-title-translation">
              <button type="button" class="detail-translate-button" :disabled="detailTitleTranslationLoading" @click="translateDetailField('title')">{{ detailTitleTranslationLoading ? '正在翻译…' : detailTitleTranslation ? '已显示中文标题' : '翻译标题' }}</button>
              <p v-if="detailTitleTranslationError" class="detail-translation-error" role="alert">{{ detailTitleTranslationError }}</p>
              <p v-if="detailTitleTranslation" class="detail-translated-title" lang="zh-CN">{{ detailTitleTranslation.text_zh }}</p>
            </div>
            <p class="detail-meta">{{ `${(detailPaper.authors || []).map((author) => author.name).filter(Boolean).join('、') || '作者信息暂缺'} · ${detailPaper.year || '年份暂缺'} · ${detailPaper.venue || 'Venue 暂缺'}` }}</p>
            <dl class="detail-identifiers">
              <div><dt>来源</dt><dd>{{ detailPaper.source }}</dd></div>
              <div v-if="detailPaper.doi"><dt>DOI</dt><dd><a v-if="detailDoiUrl" :href="detailDoiUrl" target="_blank" rel="noopener noreferrer">{{ detailPaper.doi }}</a><span v-else>{{ detailPaper.doi }}</span></dd></div>
              <div v-if="detailPaper.arxiv_id"><dt>arXiv</dt><dd>{{ detailPaper.arxiv_id }}</dd></div>
              <div v-if="detailPaper.openalex_id"><dt>OpenAlex</dt><dd>{{ detailPaper.openalex_id }}</dd></div>
              <div v-if="detailPaper.semantic_scholar_id"><dt>Semantic Scholar</dt><dd>{{ detailPaper.semantic_scholar_id }}</dd></div>
            </dl>
            <section v-if="detailPaper.abstract" class="detail-section">
              <h3>摘要</h3>
              <p>{{ detailPaper.abstract }}</p>
              <button type="button" class="detail-translate-button" :disabled="detailAbstractTranslationLoading" @click="translateDetailField('abstract')">{{ detailAbstractTranslationLoading ? '正在翻译…' : detailAbstractTranslation ? '已显示中文摘要' : '翻译摘要' }}</button>
              <p v-if="detailAbstractTranslationError" class="detail-translation-error" role="alert">{{ detailAbstractTranslationError }}</p>
              <section v-if="detailAbstractTranslation" class="detail-translated-abstract" lang="zh-CN" aria-label="中文摘要翻译">
                <strong>中文摘要</strong>
                <p>{{ detailAbstractTranslation.text_zh }}</p>
                <small>{{ `由 ${detailAbstractTranslation.model_name} 翻译` }}</small>
              </section>
            </section>
            <section v-if="detailPaper.keywords?.length" class="detail-section"><h3>关键词</h3><p>{{ detailPaper.keywords.join(' · ') }}</p></section>
            <section v-if="detailPaper.constraint_evidence?.length" class="detail-section"><h3>约束证据</h3><ul><li v-for="evidence in detailPaper.constraint_evidence" :key="evidence">{{ evidence }}</li></ul></section>
            <section v-if="detailPaper.references?.length" class="detail-section">
              <h3>来源提供的参考文献标识</h3>
              <p class="reference-description">这些是当前论文引用的上游论文在原始学术来源中的标识，不代表已收藏到“我的文献库”。</p>
              <p>{{ detailPaper.references.join(' · ') }}</p>
            </section>
            <a v-if="detailDoiUrl" class="detail-link" :href="detailDoiUrl" target="_blank" rel="noopener noreferrer">打开 DOI 页面</a>
            <a v-if="detailPublicPdfUrl" class="detail-pdf-link" :href="detailPublicPdfUrl" target="_blank" rel="noopener noreferrer">打开公开 PDF</a>
          </template>
        </aside>
      </div>
      <div v-if="comparisonResult || comparisonLoading" class="paper-detail-backdrop" @click.self="closePaperComparison">
        <aside class="paper-comparison-panel" role="dialog" aria-modal="true" aria-labelledby="paper-comparison-title">
          <button class="detail-close" type="button" aria-label="关闭论文比较" @click="closePaperComparison">×</button>
          <p class="eyebrow">SAVED PAPER COMPARISON</p>
          <h2 id="paper-comparison-title">论文事实对比</h2>
          <p v-if="comparisonLoading" class="detail-status">正在读取已保存论文与核验证据…</p>
          <p v-else-if="comparisonError" class="detail-error" role="alert">{{ comparisonError }}</p>
          <div v-else-if="comparisonResult" class="comparison-grid">
            <div class="comparison-row comparison-head" :style="{ '--comparison-count': comparisonResult.items.length }"><strong>字段</strong><strong v-for="item in comparisonResult.items" :key="item.paper_id">{{ item.title }}</strong></div>
            <div v-for="field in [{ label: '出版信息', key: 'publication' }, { label: '关键词', key: 'keywords' }, { label: '摘要', key: 'abstract' }, { label: '推荐理由', key: 'recommendation_reason' }, { label: '约束状态', key: 'constraint_status' }, { label: '核验证据', key: 'constraint_evidence' }, { label: '来源', key: 'sources' }]" :key="field.key" class="comparison-row" :style="{ '--comparison-count': comparisonResult.items.length }">
              <strong>{{ field.label }}</strong><p v-for="item in comparisonResult.items" :key="item.paper_id">{{ Array.isArray(item[field.key]) ? item[field.key].join(' · ') || '暂无' : item[field.key] || '暂无' }}</p>
            </div>
          </div>
        </aside>
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

.search-history { /* 提供可按需展开的本地运行恢复和清理入口。 */
  margin-top: 0.75rem; /* 与主搜索表单保持紧凑层级。 */
  padding: 0.75rem 0.9rem; /* 为索引条目提供舒适留白。 */
  border: 1px solid #d7e4ea; /* 使用轻量边框区别于主检索操作。 */
  border-radius: 0.75rem; /* 与页面面板保持一致圆角。 */
  background: rgba(255, 255, 255, 0.68); /* 保持历史为次级但可读的本地功能。 */
}

.search-history summary { /* 突出历史面板的可展开入口。 */
  display: flex; /* 让标题和数量在一行可扫读。 */
  align-items: center; /* 垂直对齐标题与数量。 */
  justify-content: space-between; /* 将数量置于右侧。 */
  color: #31566e; /* 使用中层级蓝色。 */
  cursor: pointer; /* 明确该标题可展开。 */
  font-size: 0.73rem; /* 保持在主搜索表单之下的视觉层级。 */
  font-weight: 800; /* 提升小字号入口辨识度。 */
}

.search-history summary span { /* 展示有限历史条目数量。 */
  padding: 0.14rem 0.42rem; /* 提供紧凑胶囊留白。 */
  border-radius: 999px; /* 使用数量胶囊强调索引规模。 */
  color: #54758a; /* 使用辅助蓝色文字。 */
  background: #e8f2f5; /* 与面板背景形成轻微层次。 */
}

.search-history > p { /* 说明历史隐私边界或展示空、错误状态。 */
  margin: 0.55rem 0 0; /* 与展开标题建立稳定距离。 */
  color: #718496; /* 使用辅助文字色。 */
  font-size: 0.67rem; /* 控制说明信息密度。 */
  line-height: 1.55; /* 提升多行状态文本可读性。 */
}

.search-history ul { /* 纵向组织最近运行索引项。 */
  display: grid; /* 使用网格保持条目间距稳定。 */
  gap: 0.45rem; /* 分隔相邻运行。 */
  margin: 0.7rem 0 0; /* 与说明文字分隔。 */
  padding: 0; /* 移除默认列表缩进。 */
  list-style: none; /* 使用卡片条目而非圆点。 */
}

.search-history li { /* 为单条历史索引提供恢复和清理操作。 */
  display: flex; /* 在宽屏并列索引信息与操作。 */
  align-items: center; /* 对齐多行元数据和按钮。 */
  justify-content: space-between; /* 将操作保持在条目右侧。 */
  gap: 0.7rem; /* 防止窄屏元数据贴近操作。 */
  padding: 0.58rem 0.65rem; /* 提供紧凑且可点击的条目留白。 */
  border-radius: 0.58rem; /* 与其它次级控件协调。 */
  background: #f5f9fb; /* 与页面背景建立轻微层次。 */
}

.search-history li > div:first-child { /* 纵向排列安全状态、轮次和停止原因。 */
  display: grid; /* 保持三行元数据清晰分隔。 */
  gap: 0.12rem; /* 减少紧凑索引的垂直占用。 */
  min-width: 0; /* 允许较长停止原因在窄屏换行。 */
}

.search-history strong { /* 突出运行状态，便于判断是否可清理。 */
  color: #31566e; /* 使用可读的中层级蓝色。 */
  font-size: 0.68rem; /* 保持与索引内容一致。 */
}

.search-history li span, .search-history li small { /* 展示轮次、更新时间和停止原因。 */
  overflow-wrap: anywhere; /* 防止长停止原因撑破窄屏。 */
  color: #718496; /* 使用辅助文字色。 */
  font-size: 0.62rem; /* 控制历史信息密度。 */
  line-height: 1.45; /* 提升多行元数据可读性。 */
}

.history-actions { /* 并列恢复与清理两个显式用户操作。 */
  display: flex; /* 横向组织操作按钮。 */
  flex: 0 0 auto; /* 不让操作区被长元数据挤压消失。 */
  gap: 0.35rem; /* 分隔恢复与清理入口。 */
}

.history-actions button { /* 设置历史恢复与清理的紧凑按钮。 */
  padding: 0.35rem 0.5rem; /* 提供适中的点击面积。 */
  border: 1px solid #b8ccdc; /* 使用蓝灰边框保持次级层级。 */
  border-radius: 0.45rem; /* 与索引条目圆角协调。 */
  color: #2e6f95; /* 使用品牌强调文字。 */
  background: #ffffff; /* 与条目背景形成可点击对比。 */
  cursor: pointer; /* 明确用户可以恢复或清理。 */
  font: inherit; /* 继承页面字体。 */
  font-size: 0.62rem; /* 保持操作紧凑。 */
  font-weight: 800; /* 提升小字号按钮可辨识度。 */
}

.history-actions .history-delete { /* 将永久清理操作显示为克制警示。 */
  border-color: #e6c9c5; /* 使用浅红边框提示不可逆性。 */
  color: #9b4b45; /* 使用克制红色文字。 */
  background: #fff8f7; /* 保持背景低饱和。 */
}

.history-actions button:disabled { /* 标记运行中或请求中的不可清理状态。 */
  cursor: default; /* 避免暗示当前可点击。 */
  opacity: 0.5; /* 视觉弱化不可用操作。 */
}

.history-message.is-error { /* 区分历史读取或清理失败与普通空状态。 */
  color: #9b4b45; /* 使用安全错误色。 */
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
  font-size: 0.75rem; /* 提升字符计数在高分辨率屏幕上的可辨识度。 */
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
  font-size: 0.88rem; /* 提升主操作文字可读性且保持按钮紧凑。 */
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

.ranking-row { /* 横向排列本地排序选项和高级条件开关。 */
  display: flex; /* 使用弹性布局。 */
  align-items: end; /* 对齐控件底部。 */
  justify-content: space-between; /* 分置模式和高级入口。 */
  gap: 1rem; /* 为窄屏换行保留间距。 */
  margin-top: 1rem; /* 与主查询框分隔。 */
}

.ranking-options { /* 在统一标准搜索下提供两个可独立选择的本地排序开关。 */
  display: grid; /* 纵向排列开关和风险提示以避免窄屏拥挤。 */
  gap: 0.35rem; /* 保持各项之间可读的紧凑间距。 */
  color: #52616b; /* 使用辅助正文色避免压过主检索操作。 */
  font-size: 0.82rem; /* 让本地模型选择和时长风险提示易于阅读。 */
}

.ranking-options label { /* 让复选框与功能名称保持可点击关联。 */
  display: inline-flex; /* 将输入控件与文本横向对齐。 */
  align-items: center; /* 保持不同浏览器复选框垂直居中。 */
  gap: 0.35rem; /* 分隔复选框和功能名称。 */
  cursor: pointer; /* 告知用户该行可直接切换。 */
}

.ranking-options input { /* 使用品牌色标识本地排序选择状态。 */
  accent-color: #2e6f95; /* 与其他表单控件的强调色保持一致。 */
}

.ranking-options p { /* 展示开启本地模型时的明确时长风险。 */
  margin: 0; /* 由网格间距统一控制垂直留白。 */
  color: #7a5b2c; /* 使用警示色提示成本而不表现为提交错误。 */
  line-height: 1.45; /* 提升较长风险说明的可读性。 */
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
  font-size: 0.82rem; /* 保持次级层级但提高触控设备上的可读性。 */
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
  font-size: 0.78rem; /* 提升高级条件标签的辨识度。 */
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
  font-size: 0.82rem; /* 提升输入内容和占位文本的可读性。 */
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
  font-size: 0.75rem; /* 避免年份范围分隔词过小。 */
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
  font-size: 0.78rem; /* 匹配放大的高级字段标签。 */
}

.web-evidence-option small { /* 说明网页发现隔离边界。 */
  color: #91a0ae; /* 使用辅助文字色。 */
  font-size: 0.72rem; /* 保持辅助层级并避免说明文字难以辨认。 */
}

.form-error { /* 展示前端校验或后端公共错误。 */
  margin: 0.9rem 0 0; /* 与表单控件分隔。 */
  padding: 0.7rem 0.8rem; /* 提供错误提示留白。 */
  border-radius: 0.65rem; /* 使用提示条圆角。 */
  color: #9b3c36; /* 使用克制红色。 */
  background: #fff0ee; /* 使用浅红背景。 */
  font-size: 0.82rem; /* 让错误提示无需放大即可阅读。 */
}

.recovery-message { /* 展示不会触发重新检索的运行恢复提示。 */
  margin: 0.9rem 0 0; /* 与表单控件和错误提示保持一致间距。 */
  padding: 0.7rem 0.8rem; /* 提供清晰可扫读的提示区域。 */
  border-radius: 0.65rem; /* 与表单错误提示保持视觉一致。 */
  color: #386277; /* 使用中性蓝色而非成功或错误色。 */
  background: #eef6fa; /* 区分于搜索结果和错误状态。 */
  font-size: 0.82rem; /* 让恢复状态说明保持清晰。 */
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

.search-usage { /* 展示同次 SQLite 快照中的实际调用与预算统计。 */
  display: grid; /* 纵向组织标题、状态和统计网格。 */
  gap: 0.45rem; /* 分隔用量区内部层级。 */
  padding-top: 0.2rem; /* 与覆盖缺口保持紧凑但清晰的距离。 */
}

.search-usage > strong { /* 标记统计来源为实际持久化快照。 */
  color: #31566e; /* 使用与过程区一致的中层级蓝色。 */
  font-size: 0.72rem; /* 保持为辅助信息而非结果主标题。 */
}

.search-usage > span { /* 展示读取中或安全错误提示。 */
  color: #718496; /* 使用克制的辅助文字色。 */
  font-size: 0.68rem; /* 控制状态信息视觉权重。 */
}

.search-usage > span[role="alert"] { /* 将用量读取失败与普通等待状态区分。 */
  color: #9b4b45; /* 使用安全且可辨识的错误色。 */
}

.search-usage dl { /* 将关键用量压缩为适合搜索页的响应式网格。 */
  display: grid; /* 使用网格稳定排列统计项。 */
  grid-template-columns: repeat(auto-fit, minmax(6.3rem, 1fr)); /* 窄屏时自动换行并保留标签和值。 */
  gap: 0.4rem; /* 分隔相邻统计项。 */
  margin: 0; /* 清除定义列表默认外边距。 */
}

.search-usage dl div { /* 为单项用量提供易扫读的轻量背景。 */
  padding: 0.42rem 0.52rem; /* 提供紧凑留白。 */
  border-radius: 0.5rem; /* 与现有统计胶囊协调。 */
  background: #eaf3f6; /* 使用浅蓝背景提示观测数据。 */
}

.search-usage dt { /* 标记 API、Token 等用量维度。 */
  color: #698093; /* 降低字段标签的视觉优先级。 */
  font-size: 0.6rem; /* 保持统计块紧凑。 */
  font-weight: 800; /* 确保小字号字段仍可辨识。 */
}

.search-usage dd { /* 展示从后端快照返回的具体统计值。 */
  margin: 0.15rem 0 0; /* 与字段标签建立紧凑间距。 */
  color: #31566e; /* 使用较深文字突出实际数值。 */
  font-size: 0.7rem; /* 保持与页面辅助统计一致。 */
  font-weight: 800; /* 让数值便于扫读。 */
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

.result-controls { /* 组织结果集合内的本地筛选与分页摘要。 */
  display: grid; /* 垂直排列筛选字段和摘要文字。 */
  gap: 0.65rem; /* 分隔筛选控件和当前页信息。 */
  padding: 0.9rem 1rem; /* 提供紧凑且独立的控制区域。 */
  border: 1px solid #d8e5ec; /* 使用浅蓝边界表达本地结果操作。 */
  border-radius: 0.85rem; /* 与结果区卡片保持一致圆角。 */
  background: rgba(255, 255, 255, 0.72); /* 轻量区分于论文卡片。 */
}

.filter-fields { /* 响应式排列四个本地筛选字段。 */
  display: grid; /* 建立均匀可收缩的字段网格。 */
  grid-template-columns: repeat(4, minmax(0, 1fr)); /* 宽屏每行展示四项。 */
  gap: 0.65rem; /* 分隔相邻筛选字段。 */
}

.filter-fields label { /* 纵向排列筛选标题和控件。 */
  display: grid; /* 建立紧凑双行结构。 */
  gap: 0.3rem; /* 分隔文字和输入控件。 */
  color: #607487; /* 使用辅助正文颜色。 */
  font-size: 0.64rem; /* 保持筛选为次级结果操作。 */
  font-weight: 800; /* 提升小字号标签可读性。 */
}

.filter-fields select,
.filter-fields input { /* 统一来源、状态和年份筛选控件。 */
  min-width: 0; /* 允许窄屏网格收缩。 */
  padding: 0.45rem 0.5rem; /* 提供可点击与可输入区域。 */
  border: 1px solid #d5e1e9; /* 保持轻量可辨识边界。 */
  border-radius: 0.5rem; /* 与结果区控件协调。 */
  color: #334e68; /* 使用正文深色。 */
  background: #fbfdfe; /* 保持输入背景干净。 */
  font: inherit; /* 继承页面字体。 */
  font-size: 0.68rem; /* 控制筛选区域信息密度。 */
}

.result-controls p { /* 展示筛选后数量和当前页。 */
  margin: 0; /* 移除默认段落空白。 */
  color: #8293a5; /* 使用辅助文字层级。 */
  font-size: 0.66rem; /* 保持摘要紧凑。 */
}

.result-pagination { /* 提供不请求后端的本地分页操作。 */
  display: flex; /* 横向排列上一页、页码和下一页。 */
  align-items: center; /* 垂直对齐分页元素。 */
  justify-content: center; /* 将分页控件置于结果列表底部中央。 */
  gap: 0.75rem; /* 分隔控件与当前页信息。 */
}

.result-pagination button { /* 设置本地分页按钮。 */
  padding: 0.5rem 0.8rem; /* 提供舒适点击面积。 */
  border: 1px solid #b8ccdc; /* 使用品牌蓝灰边界。 */
  border-radius: 0.55rem; /* 保持控件圆角一致。 */
  color: #2e6f95; /* 使用品牌交互色。 */
  background: #f3f8fb; /* 使用浅蓝背景。 */
  cursor: pointer; /* 告知用户可切换本地页面。 */
  font: inherit; /* 继承页面字体。 */
  font-size: 0.68rem; /* 保持分页操作为辅助层级。 */
  font-weight: 800; /* 增强操作可见性。 */
}

.result-pagination button:disabled { /* 标记第一页或末页不可继续切换。 */
  cursor: default; /* 阻止无效操作反馈。 */
  opacity: 0.48; /* 弱化不可用分页按钮。 */
}

.result-pagination span { /* 展示当前页与总页数。 */
  color: #607487; /* 使用辅助文字色。 */
  font-size: 0.68rem; /* 保持分页信息紧凑。 */
  font-weight: 800; /* 提升页码可扫读性。 */
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

.comparison-toolbar { /* 将小集合论文比较控制保持在搜索结果上下文内。 */
  display: flex; /* 横向排列选择摘要和操作。 */
  align-items: center; /* 垂直对齐内容。 */
  justify-content: space-between; /* 分置统计与按钮。 */
  gap: 1rem; /* 避免窄屏内容相贴。 */
  padding: 0.8rem 0.9rem; /* 提供紧凑但可点击的留白。 */
  border: 1px solid #d7e4ea; /* 与筛选区形成同层级边界。 */
  border-radius: 0.8rem; /* 保持页面控件圆角一致。 */
  background: #f8fbfc; /* 使用轻量背景区别于论文卡。 */
}

.comparison-toolbar > div { /* 组合比较标题或按钮组。 */
  display: flex; /* 横向组织内部内容。 */
  align-items: center; /* 对齐文字和按钮。 */
  flex-wrap: wrap; /* 窄屏允许换行。 */
  gap: 0.6rem; /* 保持内容可扫读。 */
}

.comparison-toolbar strong { /* 突出比较功能名称。 */
  color: #31566e; /* 使用中层级蓝色。 */
  font-size: 0.76rem; /* 保持为辅助操作。 */
}

.comparison-toolbar span, .comparison-message { /* 显示选择数量或输入边界提示。 */
  color: #718496; /* 使用辅助文字色。 */
  font-size: 0.7rem; /* 控制提示层级。 */
}

.comparison-toolbar button { /* 设置比较与清空按钮。 */
  padding: 0.45rem 0.7rem; /* 提供稳定点击区域。 */
  border: 1px solid #b8ccdc; /* 使用品牌蓝灰边框。 */
  border-radius: 0.55rem; /* 与搜索页其他按钮协调。 */
  color: #2e6f95; /* 使用品牌强调色。 */
  background: #f3f8fb; /* 使用浅蓝背景。 */
  cursor: pointer; /* 明确可点击状态。 */
  font: inherit; /* 继承页面字体。 */
  font-size: 0.68rem; /* 控制操作密度。 */
  font-weight: 800; /* 提升可发现性。 */
}

.comparison-toolbar button:disabled { /* 两篇以下或读取中不能执行比较。 */
  cursor: default; /* 弱化无效操作。 */
  opacity: 0.5; /* 清晰表达禁用状态。 */
}

.comparison-toolbar button.comparison-clear { /* 清空操作保持次级视觉。 */
  border-color: transparent; /* 移除次级操作边界。 */
  color: #718496; /* 使用辅助文字色。 */
  background: transparent; /* 减少视觉竞争。 */
}

.comparison-message { /* 显示选择或读取失败的安全提示。 */
  margin: -0.9rem 0 -0.3rem; /* 拉近比较控制区域。 */
  color: #9b4b45; /* 使用克制红色提示错误。 */
}

.paper-comparison-panel { /* 展示二至五篇论文的固定列事实对比。 */
  position: relative; /* 为关闭按钮提供定位上下文。 */
  width: min(92vw, 78rem); /* 容纳五列同时保持视口边距。 */
  height: min(88vh, 50rem); /* 保留遮罩边距并支持长内容滚动。 */
  overflow: auto; /* 允许横向与纵向查看完整对比字段。 */
  margin: auto; /* 在遮罩中居中显示对比面板。 */
  padding: 2.1rem; /* 为标题和网格内容提供留白。 */
  background: #ffffff; /* 保持文本事实的高对比阅读背景。 */
  box-shadow: 0 18px 42px rgba(15, 40, 57, 0.2); /* 与底层搜索结果建立层次。 */
}

.paper-comparison-panel h2 { /* 设置比较面板主标题。 */
  margin: 0; /* 清除默认外边距。 */
  padding-right: 2.5rem; /* 避免标题与关闭按钮重叠。 */
  color: #18354f; /* 使用页面主标题色。 */
  font-family: Georgia, "Noto Serif SC", serif; /* 延续学术阅读风格。 */
}

.comparison-grid { /* 将字段作为行、论文作为固定列组织。 */
  display: grid; /* 纵向堆叠每个字段行。 */
  min-width: 42rem; /* 在多列时允许面板横向滚动而不压缩文本。 */
  gap: 0.55rem; /* 分隔不同事实字段。 */
  margin-top: 1.2rem; /* 与比较标题分隔。 */
}

.comparison-row { /* 建立字段标签与论文值的等宽列网格。 */
  display: grid; /* 通过 CSS 变量由内联列数控制。 */
  grid-template-columns: minmax(7rem, 0.7fr) repeat(var(--comparison-count, 2), minmax(12rem, 1fr)); /* 第一列放字段名，其余列展示各论文事实。 */
  gap: 0.55rem; /* 分隔相邻列。 */
}

.comparison-row > * { /* 设置每个字段单元的公共阅读样式。 */
  margin: 0; /* 清除默认段落或标题间距。 */
  padding: 0.65rem; /* 形成可扫读单元。 */
  overflow-wrap: anywhere; /* 防止长摘要或标识撑破对比列。 */
  color: #52697d; /* 使用舒适正文色。 */
  background: #f7fafc; /* 用轻量底色区分单元。 */
  font-size: 0.7rem; /* 在多列布局中保持信息密度。 */
  line-height: 1.6; /* 提升摘要和证据可读性。 */
}

.comparison-row > strong { /* 突出字段标签。 */
  color: #31566e; /* 使用更深蓝色。 */
  background: #eef5f8; /* 与论文内容单元区分。 */
}

.comparison-head > strong { /* 突出论文标题行。 */
  color: #18354f; /* 使用主标题色。 */
  background: #e7f0f5; /* 强化列头层级。 */
}

.paper-detail-backdrop { /* 使用遮罩让详情在当前搜索上下文中保持聚焦。 */
  position: fixed; /* 覆盖滚动页面并保持关闭区域可点击。 */
  z-index: 20; /* 置于普通搜索结果之上。 */
  inset: 0; /* 填满当前视口。 */
  display: grid; /* 使用网格将详情面板定位到右侧。 */
  justify-items: end; /* 保持抽屉式阅读体验。 */
  background: rgba(18, 43, 60, 0.34); /* 使用低饱和遮罩弱化背景结果。 */
}

.paper-detail-panel { /* 展示完整规范化论文事实而不重新检索。 */
  position: relative; /* 为关闭按钮建立定位上下文。 */
  width: min(42rem, 100%); /* 限制长文本行宽并适配手机。 */
  height: 100%; /* 占满视口高度以支持长摘要滚动。 */
  overflow-y: auto; /* 仅详情内容滚动，背景保持稳定。 */
  padding: 2.1rem; /* 为标题、元数据和段落提供阅读留白。 */
  background: #ffffff; /* 保持论文详情的高对比阅读背景。 */
  box-shadow: -18px 0 38px rgba(15, 40, 57, 0.16); /* 与结果页面形成层次。 */
}

.detail-close { /* 提供稳定可见的详情关闭入口。 */
  position: absolute; /* 固定在抽屉右上角。 */
  top: 0.9rem; /* 与面板边缘保持舒适距离。 */
  right: 1rem; /* 便于鼠标和触摸操作。 */
  width: 2rem; /* 保证最小点击区域。 */
  height: 2rem; /* 保持方形点击范围。 */
  border: 1px solid #cbd9e3; /* 使用中性边框。 */
  border-radius: 50%; /* 将关闭控件显示为轻量圆形按钮。 */
  color: #486579; /* 使用辅助深蓝文字。 */
  background: #f7fafc; /* 与白色面板区分。 */
  cursor: pointer; /* 明确可关闭交互。 */
  font-size: 1.25rem; /* 提升关闭符号可见性。 */
  line-height: 1; /* 保持符号视觉居中。 */
}

.paper-detail-panel h2 { /* 设置详情标题的阅读层级。 */
  margin: 0; /* 清除默认标题外边距。 */
  padding-right: 2.5rem; /* 避免长标题与关闭按钮重叠。 */
  color: #18354f; /* 使用页面主标题色。 */
  font-family: Georgia, "Noto Serif SC", serif; /* 延续搜索页学术排版。 */
  font-size: clamp(1.35rem, 3vw, 2rem); /* 保持窄屏可读性。 */
  line-height: 1.35; /* 提升多行标题阅读体验。 */
}

.detail-title-translation { /* 组织标题下方的独立翻译操作、错误和译文。 */
  display: grid; /* 让中文标题始终排在原文标题下方。 */
  gap: 0.45rem; /* 分隔操作、错误提示与译文。 */
  margin-top: 0.65rem; /* 与原文标题保持紧凑关联。 */
}

.detail-translate-button { /* 提供详情内按字段请求翻译的轻量入口。 */
  width: fit-content; /* 保持按钮与文字长度匹配。 */
  padding: 0.42rem 0.68rem; /* 提供舒适且紧凑的点击面积。 */
  border: 1px solid #b8ccdc; /* 使用与结果卡一致的蓝灰边界。 */
  border-radius: 0.5rem; /* 延续详情面板的圆角语言。 */
  color: #2e6f95; /* 使用品牌交互色。 */
  background: #f3f8fb; /* 表明翻译为用户主动触发的辅助操作。 */
  cursor: pointer; /* 明确当前控件可点击。 */
  font-size: 0.72rem; /* 保持操作低于详情正文层级。 */
  font-weight: 800; /* 让小字号按钮仍可清晰识别。 */
}

.detail-translate-button:disabled { /* 当前字段翻译时只禁用自身按钮。 */
  cursor: default; /* 表达当前请求不可重复提交。 */
  opacity: 0.72; /* 降低进行中或已显示状态的强调度。 */
}

.detail-translation-error { /* 在对应字段附近展示安全的翻译失败提示。 */
  margin: 0; /* 由父容器统一管理垂直间距。 */
  color: #9b3c36; /* 使用克制红色提醒用户可稍后重试。 */
  font-size: 0.72rem; /* 保持错误属于局部辅助信息。 */
  line-height: 1.55; /* 允许中文错误提示自然换行。 */
}

.detail-translated-title { /* 展示不带额外前缀的中文标题译文。 */
  margin: 0; /* 由标题翻译容器控制间距。 */
  color: #2e6f95; /* 用品牌色区分原文标题和中文译文。 */
  font-size: 0.95rem; /* 让中文标题清晰但不压过原文。 */
  font-weight: 700; /* 强化中文标题的扫读辨识度。 */
  line-height: 1.65; /* 保障长标题阅读舒适。 */
}

.detail-meta, .detail-status, .detail-error { /* 统一详情辅助信息和状态文本。 */
  margin: 0.75rem 0 0; /* 与标题或标签建立稳定间距。 */
  color: #64788a; /* 使用低层级辅助文字。 */
  font-size: 0.78rem; /* 保持详情元数据紧凑。 */
  line-height: 1.6; /* 提升长作者信息可读性。 */
}

.detail-error { /* 使用安全而明显的失败样式。 */
  padding: 0.75rem; /* 增加错误提示可扫读性。 */
  border-radius: 0.65rem; /* 与页面提示保持一致。 */
  color: #9b3c36; /* 使用克制红色文字。 */
  background: #fff0ee; /* 使用浅红背景。 */
}

.detail-identifiers { /* 以紧凑网格展示来源和论文标识符。 */
  display: grid; /* 自动换行以适配不同标识数量。 */
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); /* 保证窄屏仍可阅读。 */
  gap: 0.7rem; /* 分隔身份信息块。 */
  margin: 1.2rem 0; /* 与标题和正文区分。 */
}

.detail-identifiers div { /* 为单个标识提供可扫读背景。 */
  padding: 0.65rem; /* 增加紧凑留白。 */
  border-radius: 0.65rem; /* 与其他面板元素协调。 */
  background: #f4f8fb; /* 使用低对比蓝灰背景。 */
}

.detail-identifiers dt { /* 标记标识符类型。 */
  color: #6f8799; /* 弱化字段标签。 */
  font-size: 0.63rem; /* 保持标签紧凑。 */
  font-weight: 800; /* 提升小字号辨识度。 */
}

.detail-identifiers dd { /* 展示可能较长的具体标识符。 */
  margin: 0.25rem 0 0; /* 与字段标签分隔。 */
  overflow-wrap: anywhere; /* 防止长 DOI 或平台标识撑破布局。 */
  color: #38556a; /* 使用可读的正文颜色。 */
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; /* 便于准确辨识标识符。 */
  font-size: 0.7rem; /* 控制技术文本密度。 */
}

.detail-identifiers dd a { /* 将已校验 DOI 显示为可安全解析的外部链接。 */
  color: #2e6f95; /* 使用品牌色告知用户可点击访问 DOI 解析器。 */
  text-decoration-color: #a8c3d4; /* 保持长 DOI 的下划线低干扰。 */
  text-underline-offset: 0.18em; /* 提升等宽小字号链接可读性。 */
}

.detail-section { /* 分隔摘要、关键词和证据等事实区块。 */
  margin-top: 1.2rem; /* 保持不同详情主题之间的阅读留白。 */
}

.detail-section h3 { /* 设置详情子区块标题。 */
  margin: 0; /* 清除默认外边距。 */
  color: #31566e; /* 使用中层级蓝色。 */
  font-size: 0.82rem; /* 区别于论文主标题。 */
}

.detail-section p, .detail-section ul { /* 统一正文、关键词和证据排版。 */
  margin: 0.45rem 0 0; /* 与子标题建立稳定间距。 */
  color: #52697d; /* 使用舒适正文色。 */
  font-size: 0.78rem; /* 保持详情正文可读。 */
  line-height: 1.75; /* 提升长摘要和证据阅读体验。 */
}

.detail-section ul { /* 为多条证据保留可理解的列表层级。 */
  padding-left: 1.1rem; /* 显示列表标记而不过度缩进。 */
}

.detail-section > .detail-translate-button { /* 摘要原文和翻译操作之间保留清晰间距。 */
  margin-top: 0.7rem; /* 使按钮不与原文摘要连成一体。 */
}

.detail-translated-abstract { /* 将中文摘要译文与原文明确分层展示。 */
  margin-top: 0.75rem; /* 与翻译按钮保持舒适阅读间距。 */
  padding: 0.75rem 0.85rem; /* 为长中文摘要提供稳定留白。 */
  border-left: 3px solid #7eafc4; /* 使用细色条标记机器翻译内容。 */
  border-radius: 0 0.55rem 0.55rem 0; /* 延续详情面板的圆角语言。 */
  background: #f5fafc; /* 使用浅色背景避免与原文混淆。 */
}

.detail-translated-abstract strong { /* 标记摘要译文区域。 */
  color: #2e6f95; /* 使用品牌色强调说明标签。 */
  font-size: 0.72rem; /* 保持为辅助标题层级。 */
}

.detail-translated-abstract p { /* 设置中文摘要译文正文。 */
  margin: 0.4rem 0 0; /* 与译文说明保持紧凑关联。 */
  color: #405b6d; /* 保持长文本阅读对比度。 */
  line-height: 1.8; /* 提升中文段落阅读舒适度。 */
}

.detail-translated-abstract small { /* 说明实际生成译文的模型。 */
  display: block; /* 独占一行避免打断译文正文。 */
  margin-top: 0.45rem; /* 与译文正文分隔。 */
  color: #8295a4; /* 弱化来源说明避免喧宾夺主。 */
  font-size: 0.65rem; /* 保持最低视觉层级。 */
}

.reference-description { /* 解释来源参考标识与文献库收藏的边界。 */
  color: #71899a !important; /* 与实际标识列表区分为说明文字。 */
  font-size: 0.7rem !important; /* 保持边界说明紧凑。 */
}

.detail-link { /* 标记可在新标签页打开的 DOI 解析入口。 */
  display: inline-block; /* 允许链接使用按钮式留白。 */
  margin-top: 1.35rem; /* 与详情正文拉开距离。 */
  padding: 0.55rem 0.75rem; /* 提供舒适点击区域。 */
  border-radius: 0.55rem; /* 与页面交互控件一致。 */
  color: #ffffff; /* 保持深色背景上的链接可读性。 */
  background: #2e6f95; /* 使用品牌强调色。 */
  font-size: 0.72rem; /* 控制操作层级。 */
  font-weight: 800; /* 提升链接可发现性。 */
  text-decoration: none; /* 采用按钮视觉而不是默认下划线。 */
}

.detail-pdf-link { /* 提供独立于 DOI 主入口的来源公开 PDF 访问按钮。 */
  display: inline-block; /* 让公开 PDF 链接以按钮形式呈现。 */
  margin: 0.7rem 0 0 0.55rem; /* 与 DOI 按钮并列并在窄屏自然换行。 */
  padding: 0.55rem 0.75rem; /* 与 DOI 入口保持相同点击面积。 */
  border-radius: 0.55rem; /* 延续详情抽屉的圆角语言。 */
  color: #28745a; /* 使用绿色区分公开全文与 DOI 落地页。 */
  background: #e8f7f0; /* 使用浅绿背景标记来源公开 PDF。 */
  font-size: 0.72rem; /* 保持操作层级紧凑。 */
  font-weight: 800; /* 提升入口可发现性。 */
  text-decoration: none; /* 保持按钮视觉。 */
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

  .filter-fields { /* 平板将结果筛选收敛为两列。 */
    grid-template-columns: repeat(2, minmax(0, 1fr)); /* 保证年份输入仍可用。 */
  }
}

@media (max-width: 620px) { /* 调整手机搜索页布局。 */
  .search-hero { /* 缩小手机顶部留白。 */
    padding-top: 2.5rem; /* 更快进入主搜索操作。 */
  }

  .ranking-row,
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

  .filter-fields { /* 手机将结果筛选改为单列。 */
    grid-template-columns: 1fr; /* 提升触摸输入可用性。 */
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
