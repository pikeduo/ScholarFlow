import assert from 'node:assert/strict' // 使用 Node 内置严格断言验证请求契约。
import test from 'node:test' // 使用零依赖内置测试运行器声明用例。

import { SearchApiError, comparePapers, createQueryIntent, deleteSearchRun, getCitationGraph, getPaperDetail, getSearchRunPapers, getSearchRunSynthesis, getSearchRunUsage, listSearchRuns, restoreSearchRun, searchPapers, searchWithIntent, splitTerms, streamSearchPapers, streamSearchWithIntent, translatePaperToChinese, validateQueryIntent } from '../src/services/searchApi.js' // 导入待测纯函数、REST、SSE、翻译、详情、综合报告、比较、图谱、分页、历史、用量与运行恢复入口。
import { filterSearchPapers, paginateSearchPapers, resolveSearchPageJump } from '../src/utils/searchResults.js' // 导入结果页本地筛选与分页纯函数。

const baseForm = { // 构造可复用于各用例的最小搜索表单。
  queryText: '检索 Transformer forecasting 论文', // 提供中英混合查询。
  enableSemanticRanking: false, // 默认不加载 BGE-M3 以保持标准搜索较快。
  enableCrossEncoderRanking: false, // 默认不加载 Cross Encoder 以保持标准搜索较快。
  startYear: '', // 默认不限制起始年份。
  endYear: '', // 默认不限制结束年份。
  mustInclude: '', // 默认无必须词。
  shouldInclude: '', // 默认无偏好词。
  exclude: '', // 默认无排除词。
  domains: '', // 默认无领域路由标签。
  requiresWebEvidence: false, // 默认不启用网页补充发现。
}

test('splitTerms 支持中英文分隔符并去重', () => { // 验证高级条件输入规范化。
  assert.deepEqual(splitTerms('Transformer, ETT，transformer\nbenchmark'), ['Transformer', 'ETT', 'benchmark']) // 确保保留首次词形并移除重复项。
})

test('createQueryIntent 生成后端多源检索契约', () => { // 验证表单到 QueryIntent 的核心映射。
  const intent = createQueryIntent({ ...baseForm, startYear: '2020', endYear: '2025', mustInclude: 'ETT', exclude: 'survey', domains: 'machine learning', requiresWebEvidence: true }) // 构造包含完整约束的表单。

  assert.equal(intent.query_language, 'mixed') // 验证中英混合语言识别。
  assert.deepEqual(intent.year_range, [2020, 2025]) // 验证年份转换为数字闭区间。
  assert.deepEqual(intent.must_include, ['ETT']) // 验证必须词映射。
  assert.deepEqual(intent.exclude, ['survey']) // 验证排除词映射。
  assert.deepEqual(intent.domains, ['machine learning']) // 验证动态来源领域映射。
  assert.equal(intent.target_paper_count, 20) // 验证最终目标数量与 LLM 上限一致。
  assert.equal(intent.requires_web_evidence, true) // 验证网页证据开关映射。
  assert.equal(intent.search_mode, 'standard') // 验证搜索页固定提交标准模式。
  assert.equal(intent.enable_semantic_ranking, false) // 验证 BGE-M3 默认保持关闭。
  assert.equal(intent.enable_cross_encoder_ranking, false) // 验证 Cross Encoder 默认保持关闭。
})

test('createQueryIntent 在标准搜索中支持独立开启两种本地排序', () => { // 验证页面开关不会被标准模式覆盖。
  const intent = createQueryIntent({ ...baseForm, enableSemanticRanking: true, enableCrossEncoderRanking: true }) // 构造两个本地模型均开启的标准检索表单。

  assert.equal(intent.search_mode, 'standard') // 验证本地排序选择不会改为深度模式。
  assert.equal(intent.enable_semantic_ranking, true) // 验证用户可在标准搜索中开启 BGE-M3。
  assert.equal(intent.enable_cross_encoder_ranking, true) // 验证用户可在标准搜索中开启 Cross Encoder。
})

test('createQueryIntent 拒绝不完整或倒置年份范围', () => { // 验证请求前年份错误边界。
  assert.throws(() => createQueryIntent({ ...baseForm, startYear: '2020' }), SearchApiError) // 单边年份不能提交。
  assert.throws(() => createQueryIntent({ ...baseForm, startYear: '2025', endYear: '2020' }), /起始年份不能晚于结束年份/) // 倒置年份返回明确消息。
})

test('createQueryIntent 拒绝互相冲突的包含与排除条件', () => { // 验证硬软条件冲突边界。
  assert.throws(() => createQueryIntent({ ...baseForm, mustInclude: 'Transformer', exclude: 'transformer' }), /不能同时出现在排除条件中/) // 大小写不同的相同词也应被识别。
})

test('searchPapers 提交 JSON 并返回结构化响应', async () => { // 验证成功请求参数和响应映射。
  let capturedUrl = '' // 保存替身收到的请求地址。
  let capturedOptions = null // 保存替身收到的请求配置。
  const expectedResult = { papers: [], run_state: { current_round: 1, max_rounds: 2 }, query_intent: { normalized_query: 'Transformer forecasting' } } // 构造最小多轮成功响应。
  const fetchStub = async (url, options) => { // 提供不访问网络的 fetch 替身。
    capturedUrl = url // 记录请求地址供断言。
    capturedOptions = options // 记录请求配置供断言。
    return { ok: true, status: 200, json: async () => expectedResult } // 返回固定成功响应。
  }

  const result = await searchPapers(baseForm, fetchStub, 'http://test.local') // 调用注入替身的 API 客户端。

  assert.equal(capturedUrl, 'http://test.local/api/v1/search/natural-multi-round') // 验证自然语言多轮检索接口路径。
  assert.equal(capturedOptions.method, 'POST') // 验证使用 POST 提交复杂查询。
  assert.equal(JSON.parse(capturedOptions.body).target_paper_count, 20) // 验证请求正文保留最终结果目标。
  assert.equal(JSON.parse(capturedOptions.body).query, baseForm.queryText) // 验证后端收到原始自然语言问题。
  assert.equal(result, expectedResult) // 验证成功响应原样返回页面层。
})

test('searchPapers 将后端公共错误转换为 SearchApiError', async () => { // 验证非成功 HTTP 边界。
  const fetchStub = async () => ({ ok: false, status: 503, json: async () => ({ detail: '多源论文检索服务暂时不可用，请稍后重试' }) }) // 构造不访问网络的 FastAPI 错误响应。

  await assert.rejects(() => searchPapers(baseForm, fetchStub), (error) => error instanceof SearchApiError && error.status === 503 && error.message.includes('暂时不可用')) // 验证只暴露后端已净化消息。
})

test('searchPapers 拒绝缺少论文列表的成功响应', async () => { // 验证前端依赖的最小响应契约。
  const fetchStub = async () => ({ ok: true, status: 200, json: async () => ({ query_intent: {} }) }) // 构造 HTTP 成功但缺少多轮运行状态和论文列表的响应。

  await assert.rejects(() => searchPapers(baseForm, fetchStub), /不完整的结果/) // 验证页面不会尝试渲染无效响应。
})

const editableIntent = { // 构造查询解析面板可提交的完整最小意图。
  original_query: '检索视觉语言模型医学报告生成', // 保留原始中文研究问题。
  normalized_query: 'vision language model medical report generation', // 提供英文主检索式。
  query_language: 'zh', // 标记原始语言。
  research_topics: ['vision-language model'], // 提供结构化主题。
  must_include: [], // 提供无冲突硬约束。
  should_include: ['public dataset'], // 提供软偏好。
  exclude: ['survey'], // 提供排除条件。
  target_paper_count: 20, // 设置最终结果数量。
  source_recall_count: 50, // 设置来源召回数量。
}

function createSseResponse(events) { // 构造不访问网络且可按块读取的浏览器 SSE 响应替身。
  const encoder = new TextEncoder() // 使用 UTF-8 编码模拟浏览器网络字节流。
  const stream = new ReadableStream({ // 创建标准 ReadableStream 以覆盖 fetch SSE 消费路径。
    start(controller) { // 在读取开始时依次写入测试事件。
      for (const event of events) controller.enqueue(encoder.encode(event)) // 保持每个事件可被拆分的网络分块边界。
      controller.close() // 模拟服务端在 completed 后关闭连接。
    },
  })
  return { ok: true, status: 200, body: stream } // 返回符合 streamSearch 所需最小响应契约。
}

test('searchWithIntent 直接提交编辑后的 QueryIntent 并跳过自然语言入口', async () => { // 验证重搜不会再次调用 Query Agent。
  let capturedUrl = '' // 保存直接意图请求地址。
  let capturedBody = null // 保存编辑后的请求正文。
  const expectedResult = { papers: [], run_state: { current_round: 1, max_rounds: 2 }, query_intent: editableIntent } // 构造最小多轮成功响应。
  const fetchStub = async (url, options) => { // 提供不访问网络的 fetch 替身。
    capturedUrl = url // 记录接口路径。
    capturedBody = JSON.parse(options.body) // 解析请求正文供断言。
    return { ok: true, status: 200, json: async () => expectedResult } // 返回固定响应。
  }

  const result = await searchWithIntent({ ...editableIntent, normalized_query: '  edited   english query  ' }, fetchStub, 'http://test.local') // 提交带多余空白的编辑计划。

  assert.equal(capturedUrl, 'http://test.local/api/v1/search/multi-round') // 验证跳过 `/natural` Query Agent 并进入多轮意图入口。
  assert.equal(capturedBody.normalized_query, 'edited english query') // 验证英文检索式空白被规范化。
  assert.equal(capturedBody.source_recall_count, 50) // 验证来源召回规模完整保留。
  assert.equal(result, expectedResult) // 验证成功响应返回页面层。
})

test('streamSearchPapers 消费 SSE 并按运行标识读取同次最终结果', async () => { // 验证自然语言页面不因进度显示重复提交搜索。
  const runId = 'run-stream-natural' // 构造 SSE 创建事件提供的稳定运行标识。
  const events = [] // 保存页面应收到的结构化进度事件。
  const requestUrls = [] // 记录流请求和最终结果读取请求的顺序。
  const expectedResult = { papers: [], run_state: { run_id: runId, current_round: 1, max_rounds: 2 }, query_intent: editableIntent } // 构造同次运行持久化的最小结果。
  const fetchStub = async (url) => { // 使用同一替身模拟先 POST 流、后 GET 结果的两次请求。
    requestUrls.push(url) // 保存请求地址供断言。
    if (url.endsWith('/events')) return createSseResponse([`event: run_created\ndata: {"run_id":"${runId}","event_type":"run_created"}\n\n`, `event: completed\ndata: {"run_id":"${runId}","event_type":"completed"}\n\n`]) // 返回不含论文正文的进度帧。
    return { ok: true, status: 200, json: async () => expectedResult } // 返回已持久化的完整最终结果。
  }

  const result = await streamSearchPapers(baseForm, (event) => events.push(event), fetchStub, 'http://test.local') // 执行自然语言 SSE 请求并接收页面进度回调。

  assert.deepEqual(requestUrls, ['http://test.local/api/v1/search/natural-multi-round/events', `http://test.local/api/v1/search/runs/${runId}/result`]) // 验证只执行一次搜索，第二次请求仅按运行标识读取结果。
  assert.deepEqual(events.map((event) => event.event_type), ['run_created', 'completed']) // 验证页面依序收到安全生命周期事件。
  assert.equal(result, expectedResult) // 验证页面最终渲染 REST 返回的完整结果。
})

test('streamSearchWithIntent 使用直接意图事件入口并保留编辑重搜边界', async () => { // 验证编辑后的 QueryIntent 不会触发自然语言 Query Agent。
  const runId = 'run-stream-intent' // 构造直接意图流运行标识。
  const expectedResult = { papers: [], run_state: { run_id: runId, current_round: 1, max_rounds: 2 }, query_intent: editableIntent } // 构造可供页面渲染的最小结果。
  let firstRequest = '' // 保存首个请求路径以验证直接意图入口。
  const fetchStub = async (url) => { // 根据路径返回事件流或最终结果。
    if (!firstRequest) firstRequest = url // 记录第一次请求。
    if (url.endsWith('/events')) return createSseResponse([`event: completed\ndata: {"run_id":"${runId}","event_type":"completed"}\n\n`]) // completed 事件同样可提供运行标识。
    return { ok: true, status: 200, json: async () => expectedResult } // 返回同次完成结果。
  }

  const result = await streamSearchWithIntent(editableIntent, () => {}, fetchStub, 'http://test.local') // 提交编辑意图并忽略仅用于 UI 的进度事件。

  assert.equal(firstRequest, 'http://test.local/api/v1/search/multi-round/events') // 验证直接使用编辑意图 SSE 入口。
  assert.equal(result, expectedResult) // 验证结果来自同次运行的 REST 读取。
})

test('translatePaperToChinese 只按论文标识和字段请求后端中文翻译', async () => { // 验证浏览器不会直接调用 DeepSeek 或传递论文正文。
  let capturedUrl = '' // 保存请求地址以验证稳定资源边界。
  const fetchStub = async (url, options) => { // 提供不访问网络的翻译接口替身。
    capturedUrl = url // 记录后端翻译资源路径。
    assert.equal(options.method, 'POST') // 验证翻译由显式用户操作触发。
    assert.equal(options.body, undefined) // 验证前端不提交或伪造标题摘要正文。
    return { ok: true, status: 200, json: async () => ({ paper_id: 'paper-1', field: 'title', text_zh: '中文标题', model_name: 'deepseek-v4-flash' }) } // 返回最小完整单字段翻译响应。
  }

  const translation = await translatePaperToChinese('paper-1', 'title', fetchStub, 'http://test.local') // 请求已保存论文标题的按需翻译。

  assert.equal(capturedUrl, 'http://test.local/api/v1/papers/translation/title?paper_id=paper-1') // 验证论文标识以查询参数安全传递，兼容包含斜杠的来源标识。
  assert.equal(translation.text_zh, '中文标题') // 验证单字段译文可返回给卡片展示。
  await assert.rejects(() => translatePaperToChinese('', 'title', fetchStub), /缺少需要翻译/) // 验证空标识不会进入网络层。
})

test('restoreSearchRun 先恢复状态，再读取同次已完成结果', async () => { // 验证刷新页面不会重新提交检索。
  const runId = 'saved-completed-run' // 构造 URL 中保存的运行标识。
  const state = { run_id: runId, status: 'completed', current_round: 2, query_intent: editableIntent } // 构造 SQLite 轻量状态快照。
  const expectedResult = { papers: [], run_state: { run_id: runId, current_round: 2, max_rounds: 2 }, query_intent: editableIntent } // 构造同次最终结果快照。
  const requests = [] // 记录恢复过程的只读请求顺序。
  const fetchStub = async (url, options) => { // 依路径返回状态或最终结果响应。
    requests.push({ url, method: options.method }) // 保存请求边界供断言。
    if (url.endsWith(`/runs/${runId}`)) return { ok: true, status: 200, json: async () => state } // 返回已保存轻量状态。
    return { ok: true, status: 200, json: async () => expectedResult } // 返回已保存完整结果。
  }

  const restored = await restoreSearchRun(runId, fetchStub, 'http://test.local') // 执行不产生新搜索的恢复读取。

  assert.deepEqual(requests, [{ url: `http://test.local/api/v1/search/runs/${runId}`, method: 'GET' }, { url: `http://test.local/api/v1/search/runs/${runId}/result`, method: 'GET' }]) // 验证只读取状态和同次结果资源。
  assert.equal(restored.state, state) // 验证状态快照原样返回页面层。
  assert.equal(restored.result, expectedResult) // 验证完成运行恢复同次最终结果。
})

test('restoreSearchRun 对运行中状态只恢复进度，不读取或伪造最终结果', async () => { // 验证刷新运行中页面不会重复调用来源。
  const runId = 'saved-running-run' // 构造尚未完成运行标识。
  const state = { run_id: runId, status: 'running', current_round: 1, query_intent: editableIntent } // 构造运行中轻量状态。
  let requestCount = 0 // 记录只应发生一次状态读取。
  const fetchStub = async () => { // 返回运行中状态且不提供最终结果响应。
    requestCount += 1 // 记录恢复读取次数。
    return { ok: true, status: 200, json: async () => state } // 返回固定状态快照。
  }

  const restored = await restoreSearchRun(runId, fetchStub, 'http://test.local') // 恢复运行中状态。

  assert.equal(requestCount, 1) // 验证不读取尚未就绪的结果接口。
  assert.equal(restored.state, state) // 验证页面仍可展示当前轮次与状态。
  assert.equal(restored.result, null) // 验证没有最终结果时不伪造空论文集合。
})

test('getPaperDetail 仅读取已保存论文详情并校验最小契约', async () => { // 验证详情入口不会触发新的检索请求。
  let capturedUrl = '' // 保存只读详情请求路径。
  const expectedPaper = { paper_id: 'paper-detail-1', title: 'Saved Paper', source: 'openalex', abstract: 'saved abstract' } // 构造 SQLite 已保存论文的最小响应。
  const fetchStub = async (url, options) => { // 提供不访问网络的详情读取替身。
    capturedUrl = url // 记录请求资源路径。
    assert.equal(options.method, 'GET') // 验证详情只使用只读 GET。
    return { ok: true, status: 200, json: async () => expectedPaper } // 返回固定规范化论文详情。
  }

  const paper = await getPaperDetail(' paper-detail-1 ', fetchStub, 'http://test.local') // 读取带空白的内部论文标识。

  assert.equal(capturedUrl, 'http://test.local/api/v1/papers/detail?paper_id=paper-detail-1') // 验证标识通过查询参数传递，避免 URL 型来源标识被路径分段。
  assert.equal(paper, expectedPaper) // 验证页面获得详情抽屉可渲染的统一记录。
})

test('comparePapers 限制二至五篇并提交已保存论文标识', async () => { // 验证比较入口不传递或信任前端论文事实。
  let capturedBody = null // 保存比较请求正文。
  const expectedResult = { items: [{ paper_id: 'paper-2', title: 'Paper Two' }, { paper_id: 'paper-1', title: 'Paper One' }] } // 构造按用户选择顺序返回的最小事实型对比。
  const fetchStub = async (url, options) => { // 提供不访问网络的比较读取替身。
    assert.equal(url, 'http://test.local/api/v1/compare') // 验证调用固定版本化比较路径。
    assert.equal(options.method, 'POST') // 验证使用 POST 提交小集合标识。
    capturedBody = JSON.parse(options.body) // 解析请求正文供断言。
    return { ok: true, status: 200, json: async () => expectedResult } // 返回最小完整响应。
  }

  const result = await comparePapers(['paper-2', 'paper-1'], fetchStub, 'http://test.local') // 按用户选择顺序提交两个内部标识。

  assert.deepEqual(capturedBody, { paper_ids: ['paper-2', 'paper-1'] }) // 验证请求只发送内部标识。
  assert.equal(result, expectedResult) // 验证事实型结果原样返回页面层。
  await assert.rejects(() => comparePapers(['paper-1']), /请选择 2 至 5 篇/) // 验证数量不足在网络请求前被拒绝。
  await assert.rejects(() => comparePapers(['paper-1', 'paper-1']), /不能重复/) // 验证重复论文不能占据多个比较列。
})

test('getCitationGraph 仅以重复查询参数提交已保存论文标识', async () => { // 验证图谱入口不调用外部来源或传递前端关系事实。
  let capturedUrl = '' // 保存图谱读取路径。
  const fetchStub = async (url, options) => { // 提供不访问网络的图谱读取替身。
    capturedUrl = url // 记录编码后的查询参数。
    assert.equal(options.method, 'GET') // 验证图谱为只读请求。
    return { ok: true, status: 200, json: async () => ({ nodes: [], edges: [], truncated: false, max_nodes: 30 }) } // 返回最小完整受限图响应。
  }

  await getCitationGraph(['paper-2', 'paper-1'], fetchStub, 'http://test.local') // 读取两个已保存论文节点。

  assert.equal(capturedUrl, 'http://test.local/api/v1/graph/citations?max_nodes=30&paper_ids=paper-2&paper_ids=paper-1') // 验证稳定节点上限和重复标识参数顺序。
})

test('getCitationGraph 仅显式请求可审计的版本族辅助边', async () => { // 验证前端不会把推断关系提交给后端。
  let capturedUrl = '' // 保存图谱读取路径。
  const fetchStub = async (url) => { capturedUrl = url; return { ok: true, status: 200, json: async () => ({ nodes: [], edges: [], truncated: false, max_nodes: 30 }) } } // 提供最小成功响应。

  await getCitationGraph(['paper-1'], fetchStub, 'http://test.local', 30, ['cites', 'same_work']) // 显式请求真实引用和版本族辅助事实。

  assert.equal(capturedUrl, 'http://test.local/api/v1/graph/citations?max_nodes=30&paper_ids=paper-1&edge_types=cites&edge_types=same_work') // 验证关系类型不会默认混入请求。
  await assert.rejects(() => getCitationGraph(['paper-1'], fetchStub, 'http://test.local', 30, ['semantic_similar']), /关系类型只能是 cites 或 same_work/) // 验证拒绝推断关系类型。
})

test('getSearchRunUsage 读取同次运行快照并拒绝缺失运行标识', async () => { // 验证用量入口不触发新的搜索或重新计算统计。
  let capturedUrl = '' // 保存用量读取路径。
  const expectedUsage = { run_id: 'run-1', api_call_count: 4, token_usage: 360, latency_ms: 1480, cache_hits: 2, current_round: 2, max_rounds: 3, selected_sources: ['openalex'], stop_reason: '已满足目标数量' } // 构造来自 SQLite 的最小完整观测快照。
  const fetchStub = async (url, options) => { // 提供不访问网络的只读用量替身。
    capturedUrl = url // 记录请求地址供断言。
    assert.equal(options.method, 'GET') // 验证不会提交或变更运行状态。
    return { ok: true, status: 200, json: async () => expectedUsage } // 返回固定成功响应。
  }

  const usage = await getSearchRunUsage('run-1', fetchStub, 'http://test.local') // 读取指定运行的持久化统计。

  assert.equal(capturedUrl, 'http://test.local/api/v1/usage/run-1') // 验证路径只编码并传递稳定运行标识。
  assert.equal(usage, expectedUsage) // 验证完整快照原样交给页面展示。
  await assert.rejects(() => getSearchRunUsage(''), /缺少需要读取用量/) // 验证空标识在网络请求前被拒绝。
})

test('getSearchRunSynthesis 只按运行标识读取事实型综合报告', async () => { // 验证报告入口不提交论文正文、分数或模型输入。
  let capturedUrl = '' // 保存请求地址供稳定资源断言。
  const expectedSynthesis = { run_id: 'run-1', final_paper_count: 3, sources: [], top_keywords: [], coverage_gaps: [], findings: ['已保存结论'], follow_up_suggestions: [] } // 构造页面依赖的最小完整事实报告。
  const fetchStub = async (url, options) => { // 提供不访问网络的只读响应替身。
    capturedUrl = url // 记录编码后的只读资源路径。
    assert.equal(options.method, 'GET') // 验证报告读取不会写入或触发搜索。
    return { ok: true, status: 200, json: async () => expectedSynthesis } // 返回固定已保存报告。
  }

  const synthesis = await getSearchRunSynthesis('run-1', fetchStub, 'http://test.local') // 读取指定运行的事实型报告。

  assert.equal(capturedUrl, 'http://test.local/api/v1/search/runs/run-1/synthesis') // 验证只传递稳定运行标识。
  assert.equal(synthesis, expectedSynthesis) // 验证通过最小契约的报告原样返回页面。
  await assert.rejects(() => getSearchRunSynthesis('', fetchStub), /缺少需要读取综合报告/) // 验证空标识不会进入网络层。
})

test('getSearchRunPapers 提交服务端筛选排序和分页参数', async () => { // 验证页面不再依赖前端本地切片作为唯一事实源。
  let capturedUrl = '' // 保存经过安全编码的只读查询地址。
  const expectedPage = { run_id: 'run-1', items: [{ paper_id: 'paper-2' }], total: 1, page: 1, page_size: 5, total_pages: 1 } // 构造最小完整服务端分页响应。
  const fetchStub = async (url, options) => { // 提供不访问网络的分页读取替身。
    capturedUrl = url // 记录请求地址供断言。
    assert.equal(options.method, 'GET') // 验证筛选和排序不会改写结果快照。
    return { ok: true, status: 200, json: async () => expectedPage } // 返回固定结果页。
  }

  const page = await getSearchRunPapers('run-1', { source: 'openalex', relevance: 'satisfied', yearStart: '2020', yearEnd: '2025', sort: 'year_desc', page: 1, pageSize: 5 }, fetchStub, 'http://test.local') // 请求完整筛选与排序组合。

  assert.equal(capturedUrl, 'http://test.local/api/v1/search/runs/run-1/papers?page=1&page_size=5&sort=year_desc&source=openalex&relevance=satisfied&year_start=2020&year_end=2025') // 验证查询参数固定、完整且顺序稳定。
  assert.equal(page, expectedPage) // 验证服务端分页响应原样返回给页面。
  await assert.rejects(() => getSearchRunPapers('', {}, fetchStub), /缺少需要读取结果/) // 验证空运行标识在网络请求前被拒绝。
})

test('listSearchRuns 与 deleteSearchRun 只处理受控本地运行索引', async () => { // 验证历史读取包含搜索问题，清理只接受 204 成功响应。
  const calls = [] // 保存替身收到的只读和显式删除请求。
  const fetchStub = async (url, options) => { // 提供不访问网络的运行历史替身。
    calls.push({ url, options }) // 记录请求以验证稳定资源路径和方法。
    if (options.method === 'GET') return { ok: true, status: 200, json: async () => ({ items: [{ run_id: 'run-1', query_text: '测试搜索问题', status: 'completed', result_ready: true }], limit: 10 }) } // 返回包含搜索问题但不含论文内容的最小历史页。
    return { ok: true, status: 204, json: async () => ({}) } // 返回符合删除契约的无正文状态码。
  }

  const history = await listSearchRuns(10, fetchStub, 'http://test.local') // 读取最近十条运行索引。
  await deleteSearchRun('run-1', fetchStub, 'http://test.local') // 显式清理用户已选择的终态运行。

  assert.equal(history.items[0].run_id, 'run-1') // 验证历史响应可供恢复入口使用。
  assert.equal(history.items[0].query_text, '测试搜索问题') // 验证前端保留历史项展示所需搜索问题。
  assert.equal(calls[0].url, 'http://test.local/api/v1/search/runs?limit=10') // 验证历史只按受控数量读取本地索引。
  assert.equal(calls[1].options.method, 'DELETE') // 验证清理由显式 DELETE 请求触发。
  await assert.rejects(() => listSearchRuns(0, fetchStub), /数量上限必须在 1 至 50/) // 验证无效数量不进入网络层。
  await assert.rejects(() => deleteSearchRun('', fetchStub), /缺少需要清理/) // 验证空运行标识不进入删除边界。
})

test('filterSearchPapers 按来源、年份与核验状态筛选且保持原始排序', () => { // 验证本地筛选不改变后端相关性排序或发起新请求。
  const papers = [
    { paper_id: 'paper-1', source: 'openalex', year: 2024, constraint_status: 'satisfied' },
    { paper_id: 'paper-2', source: 'semantic_scholar', year: 2021, constraint_status: 'uncertain' },
    { paper_id: 'paper-3', source: 'openalex', year: 2018, constraint_status: 'satisfied' },
  ] // 构造按相关性已排序的最终论文集合。

  const filtered = filterSearchPapers(papers, { source: 'openalex', relevance: 'satisfied', yearStart: '2020', yearEnd: '' }) // 组合来源、核验和年份起点筛选。

  assert.deepEqual(filtered.map((paper) => paper.paper_id), ['paper-1']) // 验证只保留全部条件满足的论文且顺序不变。
})

test('paginateSearchPapers 校正越界页码并保留稳定页面摘要', () => { // 验证分页不依赖后端接口也不会产生越界空页。
  const papers = [{ paper_id: 'paper-1' }, { paper_id: 'paper-2' }, { paper_id: 'paper-3' }] // 构造三篇已筛选论文。

  const page = paginateSearchPapers(papers, 9, 2) // 请求远超总页数的页码。

  assert.equal(page.page, 2) // 验证页码被校正为最后一页。
  assert.equal(page.totalPages, 2) // 验证总页数按固定页大小计算。
  assert.deepEqual(page.items.map((paper) => paper.paper_id), ['paper-3']) // 验证最后一页保留剩余论文。
})

test('resolveSearchPageJump 只接受当前结果范围内的整数页码', () => { // 验证页码跳转不会提交越界或非整数请求。
  assert.equal(resolveSearchPageJump('3', 5), 3) // 验证范围内页码可直接用于跳转。
  assert.equal(resolveSearchPageJump(' 5 ', 5), 5) // 验证首尾空白不会影响有效整数输入。
  assert.equal(resolveSearchPageJump('0', 5), null) // 验证第一页之前的页码被拒绝。
  assert.equal(resolveSearchPageJump('6', 5), null) // 验证超过总页数的输入被拒绝。
  assert.equal(resolveSearchPageJump('1.5', 5), null) // 验证小数不会被错误截断为页码。
})

test('validateQueryIntent 拒绝倒置年份、候选不足和条件冲突', () => { // 验证编辑重搜的关键错误边界。
  assert.throws(() => validateQueryIntent({ ...editableIntent, year_range: [2026, 2020] }), /起始年份不能晚于结束年份/) // 拒绝倒置年份。
  assert.throws(() => validateQueryIntent({ ...editableIntent, source_recall_count: 10 }), /不少于最终结果/) // 拒绝来源候选少于最终数量。
  assert.throws(() => validateQueryIntent({ ...editableIntent, must_include: ['ETT'], exclude: ['ett'] }), /不能同时出现在排除条件中/) // 拒绝大小写无关冲突。
})

test('validateQueryIntent 支持 Vue 响应式嵌套字段', () => { // 验证编辑面板传入的 Proxy 不会导致泛化提交失败。
  const reactiveTopics = new Proxy(['vision-language model'], {}) // 模拟 Vue 在组件 props 中暴露的响应式数组。
  const validated = validateQueryIntent({ ...editableIntent, research_topics: reactiveTopics }) // 提交含响应式嵌套字段的完整意图。

  assert.deepEqual(validated.research_topics, ['vision-language model']) // 验证回退深复制后仍保留可序列化的主题条件。
})
