/** 对已完成搜索结果执行本地筛选和分页，绝不触发新的检索请求。 */

/** 按来源、年份与核验状态筛选论文，保留后端返回的相关性排序。 */
export function filterSearchPapers(papers, filters = {}) { // 接收稳定最终结果和页面可编辑筛选条件。
  const source = String(filters.source || 'all') // 读取来源筛选，默认不过滤。
  const relevance = String(filters.relevance || 'all') // 读取核验状态筛选，默认不过滤。
  const yearStart = Number(filters.yearStart || 0) // 将可选起始年份转换为数字边界。
  const yearEnd = Number(filters.yearEnd || 0) // 将可选结束年份转换为数字边界。
  return (Array.isArray(papers) ? papers : []).filter((paper) => { // 防御空结果并按原数组顺序过滤。
    if (source !== 'all' && paper.source !== source) return false // 仅保留用户选择的学术来源。
    if (relevance !== 'all' && paper.constraint_status !== relevance) return false // 仅保留指定核验状态。
    if (yearStart && (!Number.isInteger(paper.year) || paper.year < yearStart)) return false // 起始年份要求缺失年份不视为满足。
    if (yearEnd && (!Number.isInteger(paper.year) || paper.year > yearEnd)) return false // 结束年份要求缺失年份不视为满足。
    return true // 所有已设置条件均满足时保留论文。
  })
}

/** 将已筛选论文切分为固定页大小，并校正越界页码。 */
export function paginateSearchPapers(papers, page = 1, pageSize = 5) { // 接收筛选后的论文集合与页面状态。
  const items = Array.isArray(papers) ? papers : [] // 防御空集合或异常输入。
  const safePageSize = Number.isInteger(pageSize) && pageSize > 0 ? pageSize : 5 // 保证每页数量始终为正整数。
  const total = items.length // 保存筛选后总数供页面摘要展示。
  const totalPages = Math.max(1, Math.ceil(total / safePageSize)) // 空结果仍保留第一页以简化控件状态。
  const safePage = Math.min(Math.max(Number.isInteger(page) ? page : 1, 1), totalPages) // 将无效或越界页码收敛到合法范围。
  const start = (safePage - 1) * safePageSize // 计算当前页第一条论文偏移。
  return { items: items.slice(start, start + safePageSize), total, page: safePage, pageSize: safePageSize, totalPages } // 返回渲染和分页控件所需稳定快照。
}

/** 将用户输入的页码严格校验为当前分页范围内的目标页。 */
export function resolveSearchPageJump(value, totalPages) { // 接收页码输入文本和当前服务端返回的总页数。
  const normalizedValue = String(value ?? '').trim() // 规范化输入框中的空白和数值文本。
  const normalizedTotalPages = Number.isInteger(totalPages) && totalPages > 0 ? totalPages : 1 // 防御页面摘要尚未加载时的异常页数。
  if (!/^\d+$/.test(normalizedValue)) return null // 拒绝小数、负数、科学计数法和非数字输入。
  const page = Number(normalizedValue) // 在通过整数文本校验后转换为数值。
  if (!Number.isSafeInteger(page) || page < 1 || page > normalizedTotalPages) return null // 拒绝超出当前结果范围的页码。
  return page // 返回可直接交给既有服务端分页流程的目标页。
}
