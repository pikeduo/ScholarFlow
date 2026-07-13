import assert from 'node:assert/strict' // 使用严格断言验证文献库请求契约。
import test from 'node:test' // 使用 Node 内置测试运行器避免新增依赖。

import { deleteLibraryItem, LibraryApiError, listLibraryItems, normalizeKeywords, saveLibraryPaper, searchLibraryItemsSemantically, updateLibraryItem } from '../src/services/libraryApi.js' // 导入待测文献库客户端。

const paper = { paper_id: 'paper-1', title: 'Evidence Retrieval', source: 'openalex' } // 构造最小 PaperRecord 请求片段。
const item = { item_id: 'item-1', paper, keywords: ['重点'], note: null, reading_status: 'unread', saved_at: '2026-07-12T00:00:00Z', updated_at: '2026-07-12T00:00:00Z' } // 构造稳定收藏响应。

test('normalizeKeywords 支持文本分隔并进行大小写无关去重', () => { // 验证关键词表单规范化。
  assert.deepEqual(normalizeKeywords('LLM, 检索，llm\n重点'), ['LLM', '检索', '重点']) // 保留首次显示形式并移除空值和重复项。
})

test('saveLibraryPaper 提交完整论文并解析去重结果', async () => { // 验证搜索结果收藏契约。
  let capturedUrl = '' // 保存请求地址。
  let capturedBody = null // 保存请求正文。
  const fetchStub = async (url, options) => { // 提供不访问网络的 fetch 替身。
    capturedUrl = url // 记录端点。
    capturedBody = JSON.parse(options.body) // 解析 JSON 正文。
    return { ok: true, status: 200, json: async () => ({ item, created: true }) } // 返回固定新建结果。
  }

  const result = await saveLibraryPaper(paper, { keywords: ['重点'], readingStatus: 'reading' }, fetchStub, 'http://test.local') // 调用收藏客户端。

  assert.equal(capturedUrl, 'http://test.local/api/v1/library/items') // 验证稳定收藏路径。
  assert.equal(capturedBody.paper.paper_id, 'paper-1') // 验证完整论文进入后端快照。
  assert.deepEqual(capturedBody.keywords, ['重点']) // 验证关键词映射。
  assert.equal(capturedBody.reading_status, 'reading') // 验证阅读状态字段命名。
  assert.equal(result.created, true) // 验证保存结果原样返回。
})

test('listLibraryItems 正确编码结构化筛选、排序和分页', async () => { // 验证文献库筛选请求。
  let capturedUrl = '' // 保存筛选 URL。
  const fetchStub = async (url) => { // 提供固定列表响应。
    capturedUrl = url // 记录带查询参数的地址。
    return { ok: true, status: 200, json: async () => ({ items: [item], total: 1, page: 2, page_size: 5, total_pages: 3, keyword_facets: [] }) } // 返回一条收藏和服务端分页元数据。
  }

  const result = await listLibraryItems({ keyword: '时间 序列', readingStatus: 'unread', yearStart: 2020, yearEnd: 2025, venue: 'NeurIPS', sort: 'year_desc' }, { page: 2, pageSize: 5 }, fetchStub, 'http://test.local') // 提交关键词、结构化筛选和分页。

  assert.equal(capturedUrl, 'http://test.local/api/v1/library/items?keyword=%E6%97%B6%E9%97%B4+%E5%BA%8F%E5%88%97&reading_status=unread&year_start=2020&year_end=2025&venue=NeurIPS&sort=year_desc&page=2&page_size=5') // 验证 URLSearchParams 编码和字段名称。
  assert.equal(result.total, 1) // 验证列表结果返回。
  assert.equal(result.page, 2) // 验证分页元数据可供页面读取。
})

test('searchLibraryItemsSemantically 提交自然语言、关键词筛选并解析相似度结果', async () => { // 验证文献库语义检索契约。
  let capturedUrl = '' // 保存语义检索请求地址。
  const fetchStub = async (url) => { // 提供固定语义检索响应。
    capturedUrl = url // 记录携带查询和筛选条件的端点。
    return { ok: true, status: 200, json: async () => ({ items: [{ item, semantic_score: 0.86 }], total: 1, degraded: false, degradation_reason: null }) } // 返回后端稳定语义响应。
  }

  const result = await searchLibraryItemsSemantically('语义检索', { keyword: '重点', readingStatus: 'unread', yearStart: 2020, yearEnd: 2025, venue: 'ACL' }, { topK: 10 }, fetchStub, 'http://test.local') // 调用自然语言检索客户端。

  assert.equal(capturedUrl, 'http://test.local/api/v1/library/items/semantic-search?query=%E8%AF%AD%E4%B9%89%E6%A3%80%E7%B4%A2&top_k=10&keyword=%E9%87%8D%E7%82%B9&reading_status=unread&year_start=2020&year_end=2025&venue=ACL') // 验证端点、查询、数量和筛选编码。
  assert.equal(result.items[0].semantic_score, 0.86) // 验证相似度结果完整返回。
})

test('updateLibraryItem 仅提交明确变更并编码资源 ID', async () => { // 验证局部更新契约。
  let capturedUrl = '' // 保存更新路径。
  let capturedBody = null // 保存更新正文。
  const fetchStub = async (url, options) => { // 提供固定更新响应。
    capturedUrl = url // 记录编码后的资源路径。
    capturedBody = JSON.parse(options.body) // 解析局部更新正文。
    return { ok: true, status: 200, json: async () => ({ ...item, reading_status: 'read', note: '' }) } // 返回更新后记录。
  }

  await updateLibraryItem('item/1', { note: '', readingStatus: 'read' }, fetchStub, 'http://test.local') // 提交不含关键词的变更。

  assert.equal(capturedUrl, 'http://test.local/api/v1/library/items/item%2F1') // 验证内部 ID 被安全编码。
  assert.deepEqual(capturedBody, { note: '', reading_status: 'read' }) // 验证未提交字段不会被意外清空。
})

test('deleteLibraryItem 接受 204 无正文响应', async () => { // 验证删除接口不会解析空 JSON。
  let capturedMethod = '' // 保存 HTTP 方法。
  const fetchStub = async (url, options) => { // 提供无正文成功响应。
    capturedMethod = options.method // 记录删除方法。
    return { ok: true, status: 204 } // 模拟 FastAPI 204。
  }

  const result = await deleteLibraryItem('item-1', fetchStub, 'http://test.local') // 删除指定记录。

  assert.equal(capturedMethod, 'DELETE') // 验证使用 DELETE。
  assert.equal(result, undefined) // 验证客户端不虚构删除响应。
})

test('文献库客户端暴露后端安全错误且拒绝不完整响应', async () => { // 验证 HTTP 和响应契约错误边界。
  const failedFetch = async () => ({ ok: false, status: 404, json: async () => ({ detail: '文献库记录不存在' }) }) // 构造后端公共 404。
  const incompleteFetch = async () => ({ ok: true, status: 200, json: async () => ({ items: [] }) }) // 构造缺少 total 的响应。

  await assert.rejects(() => deleteLibraryItem('missing', failedFetch), (error) => error instanceof LibraryApiError && error.status === 404) // 验证保留安全消息和状态码。
  await assert.rejects(() => listLibraryItems({}, incompleteFetch), /不完整的列表/) // 验证页面不会渲染不完整响应。
})
