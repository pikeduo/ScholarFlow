import assert from 'node:assert/strict' // 使用 Node 内置严格断言验证请求契约。
import test from 'node:test' // 使用零依赖内置测试运行器声明用例。

import { SearchApiError, createQueryIntent, searchPapers, searchWithIntent, splitTerms, validateQueryIntent } from '../src/services/searchApi.js' // 导入待测纯函数和两种 API 入口。

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

test('validateQueryIntent 拒绝倒置年份、候选不足和条件冲突', () => { // 验证编辑重搜的关键错误边界。
  assert.throws(() => validateQueryIntent({ ...editableIntent, year_range: [2026, 2020] }), /起始年份不能晚于结束年份/) // 拒绝倒置年份。
  assert.throws(() => validateQueryIntent({ ...editableIntent, source_recall_count: 10 }), /不少于最终结果/) // 拒绝来源候选少于最终数量。
  assert.throws(() => validateQueryIntent({ ...editableIntent, must_include: ['ETT'], exclude: ['ett'] }), /不能同时出现在排除条件中/) // 拒绝大小写无关冲突。
})
