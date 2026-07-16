import { requestApiJson } from './apiClient.js' // 复用无业务语义的 HTTP、错误结构和 JSON 解析边界。

/** 表示个人文献库请求失败且可安全展示的公共错误。 */
export class LibraryApiError extends Error { // 继承标准错误供页面统一捕获。
  constructor(message, status = null) { // 接收展示消息和可选 HTTP 状态码。
    super(message) // 保存安全错误说明。
    this.name = 'LibraryApiError' // 提供稳定错误类型名称。
    this.status = status // 保存状态码供界面判断记录是否不存在。
  }
}

const DEFAULT_API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL || '').replace(/\/$/, '') // 默认使用 Vite 代理并允许部署覆盖 API 根地址。

/** 将论文保存到个人文献库并返回去重结果。 */
export async function saveLibraryPaper(paper, options = {}, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许测试注入离线 fetch。
  if (!paper || typeof paper !== 'object' || !paper.paper_id) throw new LibraryApiError('论文信息不完整，无法收藏') // 在网络调用前拒绝无稳定身份论文。
  const requestBody = { // 只提交后端收藏契约允许的字段。
    paper, // 保留完整 PaperRecord 供 SQLite 建立论文快照。
    keywords: normalizeKeywords(options.keywords || options.tags || []), // 新接口使用关键词，兼容旧调用方的标签选项。
    note: options.note ?? null, // 保留可选备注。
    reading_status: options.readingStatus || 'unread', // 新收藏默认未读。
  }
  const result = await requestLibrary('/api/v1/library/items', { method: 'POST', body: JSON.stringify(requestBody) }, fetchImpl, apiBaseUrl) // 调用去重保存端点。
  if (!result?.item || typeof result.created !== 'boolean') throw new LibraryApiError('文献库服务返回了不完整的保存结果') // 验证页面依赖的最小响应。
  return result // 返回收藏记录和新建标记。
}

/** 查询可按关键词和阅读状态筛选的文献库列表。 */
export async function listLibraryItems(filters = {}, options = {}, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 接收筛选器、分页选项和测试替身。
  if (typeof options === 'function') { // 兼容旧调用方将 fetch 实现作为第二个参数传入。
    apiBaseUrl = fetchImpl // 保留旧签名的 API 地址参数。
    fetchImpl = options // 将旧第二参数恢复为 fetch 实现。
    options = {} // 旧调用方未传分页选项时使用后端默认值。
  }
  const params = new URLSearchParams() // 使用浏览器标准编码查询参数。
  if (String(filters.keyword || '').trim()) params.set('keyword', String(filters.keyword).trim()) // 仅提交由关键词面板选中的有效关键词。
  if (filters.readingStatus) params.set('reading_status', filters.readingStatus) // 仅提交明确阅读状态。
  if (filters.yearStart) params.set('year_start', String(filters.yearStart)) // 提交可选年份下限。
  if (filters.yearEnd) params.set('year_end', String(filters.yearEnd)) // 提交可选年份上限。
  if (String(filters.venue || '').trim()) params.set('venue', String(filters.venue).trim()) // 提交期刊或会议名称的包含筛选。
  if (filters.sort) params.set('sort', filters.sort) // 提交用户选择的稳定展示排序。
  if (options.page) params.set('page', String(options.page)) // 请求指定的服务端页码。
  if (options.pageSize) params.set('page_size', String(options.pageSize)) // 请求指定的服务端每页数量。
  const suffix = params.toString() ? `?${params.toString()}` : '' // 空筛选时避免多余问号。
  const result = await requestLibrary(`/api/v1/library/items${suffix}`, { method: 'GET' }, fetchImpl, apiBaseUrl) // 获取筛选结果。
  if (!result || !Array.isArray(result.items) || typeof result.total !== 'number' || !Number.isInteger(result.page) || !Number.isInteger(result.page_size) || !Number.isInteger(result.total_pages) || !Array.isArray(result.keyword_facets)) throw new LibraryApiError('文献库服务返回了不完整的列表') // 防止页面渲染无效响应。
  if (result.keyword_facets.some((facet) => !facet?.keyword || !Number.isInteger(facet.count) || facet.count < 1)) throw new LibraryApiError('文献库关键词筛选结果不完整') // 防止页面渲染无法选择的关键词面板。
  return result // 返回稳定列表与总数。
}

/** 使用自然语言在当前关键词和阅读状态筛选范围内检索收藏论文。 */
export async function searchLibraryItemsSemantically(query, filters = {}, options = {}, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 接收查询、结构化筛选、结果数量和测试替身。
  const normalizedQuery = String(query || '').trim() // 清理用户输入首尾空白。
  if (normalizedQuery.length < 2) throw new LibraryApiError('请输入至少两个字符的文献库检索内容') // 在网络请求前阻止无意义自然语言查询。
  const topK = Number(options.topK || 20) // 读取可选结果数量并保持默认二十篇。
  if (!Number.isInteger(topK) || topK < 1 || topK > 50) throw new LibraryApiError('文献库语义检索结果数量必须在 1 到 50 之间') // 对齐后端 Query 参数边界。
  const params = new URLSearchParams({ query: normalizedQuery, top_k: String(topK) }) // 使用浏览器标准编码文本和数值查询参数。
  if (String(filters.keyword || '').trim()) params.set('keyword', String(filters.keyword).trim()) // 保留当前关键词筛选范围。
  if (filters.readingStatus) params.set('reading_status', filters.readingStatus) // 保留当前阅读状态筛选范围。
  if (filters.yearStart) params.set('year_start', String(filters.yearStart)) // 保留年份下限筛选范围。
  if (filters.yearEnd) params.set('year_end', String(filters.yearEnd)) // 保留年份上限筛选范围。
  if (String(filters.venue || '').trim()) params.set('venue', String(filters.venue).trim()) // 保留期刊或会议筛选范围。
  const result = await requestLibrary(`/api/v1/library/items/semantic-search?${params.toString()}`, { method: 'GET' }, fetchImpl, apiBaseUrl) // 调用版本化自然语言语义检索端点。
  if (!result || !Array.isArray(result.items) || typeof result.total !== 'number' || typeof result.degraded !== 'boolean') throw new LibraryApiError('文献库语义检索服务返回了不完整的结果') // 防止页面渲染不完整或不兼容响应。
  if (result.items.some((entry) => !entry?.item?.item_id || typeof entry.semantic_score !== 'number')) throw new LibraryApiError('文献库语义检索结果缺少论文或相似度信息') // 验证每个结果可安全转换为页面卡片。
  return result // 返回论文、相似度和安全降级状态。
}

/** 更新收藏关键词、备注或阅读状态。 */
export async function updateLibraryItem(itemId, changes, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 接收内部收藏 ID 和明确变更。
  if (!itemId) throw new LibraryApiError('缺少文献库记录标识') // 阻止构造无效资源路径。
  const requestBody = {} // 只序列化调用方明确提供的字段。
  if (Object.hasOwn(changes, 'keywords')) requestBody.keywords = normalizeKeywords(changes.keywords || []) // 支持清空或替换关键词。
  if (Object.hasOwn(changes, 'note')) requestBody.note = changes.note // 支持文本或 null 清空备注。
  if (Object.hasOwn(changes, 'readingStatus')) requestBody.reading_status = changes.readingStatus // 映射前端字段到后端契约。
  const result = await requestLibrary(`/api/v1/library/items/${encodeURIComponent(itemId)}`, { method: 'PATCH', body: JSON.stringify(requestBody) }, fetchImpl, apiBaseUrl) // 提交局部更新。
  if (!result?.item_id || !result.paper) throw new LibraryApiError('文献库服务返回了不完整的记录') // 验证更新响应。
  return result // 返回更新后的完整收藏。
}

/** 删除指定文献库记录。 */
export async function deleteLibraryItem(itemId, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 接收内部收藏 ID。
  if (!itemId) throw new LibraryApiError('缺少文献库记录标识') // 阻止无效删除请求。
  await requestLibrary(`/api/v1/library/items/${encodeURIComponent(itemId)}`, { method: 'DELETE' }, fetchImpl, apiBaseUrl, true) // 允许 204 无正文响应。
}

/** 清理关键词并执行大小写无关去重。 */
export function normalizeKeywords(keywords) { // 接收关键词数组或逗号分隔文本。
  const values = Array.isArray(keywords) ? keywords : String(keywords || '').split(/[,，\n]/) // 同时支持表单文本和数组。
  const seen = new Set() // 保存大小写无关比较键。
  return values.map((tag) => String(tag).trim()).filter((tag) => { // 清除空白并保留首次有效标签。
    const key = tag.toLocaleLowerCase() // 生成稳定比较键。
    if (!tag || seen.has(key)) return false // 跳过空值和重复项。
    seen.add(key) // 标记当前标签已接受。
    return true // 保留首次显示形式。
  })
}

/** 兼容旧页面或插件对标签工具函数的导入，新增调用应使用关键词名称。 */
export const normalizeTags = normalizeKeywords // 避免仅因命名升级破坏现有前端调用方。

/** 执行文献库 HTTP 请求并统一解析公共错误。 */
async function requestLibrary(path, options, fetchImpl, apiBaseUrl, allowEmpty = false) { // 复用所有文献库请求的网络边界。
  return requestApiJson(path, { ...options, headers: options.body ? { 'Content-Type': 'application/json' } : undefined }, { fetchImpl, apiBaseUrl, ErrorType: LibraryApiError, networkMessage: '无法连接文献库服务，请确认后端已启动', unavailableMessage: '文献库服务暂时不可用，请稍后重试', notFoundMessage: '文献库记录不存在', unsupportedNetworkMessage: '当前环境不支持网络请求', allowEmpty, invalidJsonMessage: '文献库服务返回了无法解析的结果' }) // 保留文献库既有错误文案与 204 语义，仅下沉重复 HTTP 处理。
}
