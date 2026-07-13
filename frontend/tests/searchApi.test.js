import assert from 'node:assert/strict' // 使用 Node 内置严格断言验证请求契约。
import test from 'node:test' // 使用零依赖内置测试运行器声明用例。

import { SearchApiError, createQueryIntent, restoreSearchRun, searchPapers, searchWithIntent, splitTerms, streamSearchPapers, streamSearchWithIntent, validateQueryIntent } from '../src/services/searchApi.js' // 导入待测纯函数、REST、SSE 与运行恢复入口。
import { filterSearchPapers, paginateSearchPapers } from '../src/utils/searchResults.js' // 导入结果页本地筛选与分页纯函数。

const baseForm = { // 构造可复用于各用例的最小搜索表单。
  queryText: '检索 Transformer forecasting 论文', // 提供中英混合查询。
  searchMode: 'standard', // 使用默认标准模式。
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

test('validateQueryIntent 拒绝倒置年份、候选不足和条件冲突', () => { // 验证编辑重搜的关键错误边界。
  assert.throws(() => validateQueryIntent({ ...editableIntent, year_range: [2026, 2020] }), /起始年份不能晚于结束年份/) // 拒绝倒置年份。
  assert.throws(() => validateQueryIntent({ ...editableIntent, source_recall_count: 10 }), /不少于最终结果/) // 拒绝来源候选少于最终数量。
  assert.throws(() => validateQueryIntent({ ...editableIntent, must_include: ['ETT'], exclude: ['ett'] }), /不能同时出现在排除条件中/) // 拒绝大小写无关冲突。
})
