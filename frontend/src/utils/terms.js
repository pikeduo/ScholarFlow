const TERM_SEPARATOR = /[,，\n]/ // 统一支持英文逗号、中文逗号和换行作为用户输入的词项分隔符。

/**
 * 将已有词项序列清理为保持首次词形和顺序的有限唯一列表。
 *
 * @param {unknown[]} values 已分隔的词项序列。
 * @param {number} maxItems 最多保留的有效词项数量。
 * @returns {string[]} 清除空白并按大小写无关规则去重后的词项。
 */
export function deduplicateTerms(values, maxItems = Number.POSITIVE_INFINITY) { // 仅处理已有序列，绝不拆分数组元素中的逗号。
  const limit = Number.isFinite(maxItems) ? Math.max(0, Math.floor(maxItems)) : Number.POSITIVE_INFINITY // 将展示上限规范化为非负整数或无限制。
  if (limit === 0) return [] // 明确支持调用方主动隐藏全部词项的边界。
  const seen = new Set() // 保存大小写无关比较键以保留第一次出现的展示词形。
  const terms = [] // 保存按输入顺序接受的有效词项。
  for (const value of Array.isArray(values) ? values : []) { // 非数组输入稳定视为空序列，避免调用方意外遍历字符串字符。
    const term = String(value || '').trim() // 统一清理空值和词项两端空白。
    const key = term.toLocaleLowerCase() // 生成大小写无关的比较键。
    if (!term || seen.has(key)) continue // 跳过空词项和已出现的等价词项。
    seen.add(key) // 标记当前词项已经接受。
    terms.push(term) // 保留用户首次输入的可读词形和原始顺序。
    if (terms.length >= limit) break // 到达展示上限后停止遍历，避免无用处理长列表。
  }
  return terms // 返回可直接用于 API 请求或界面标签的安全词项列表。
}

/**
 * 将逗号或换行分隔的自由文本转换为唯一词项列表。
 *
 * @param {unknown} value 用户输入的自由文本。
 * @param {number} maxItems 最多保留的有效词项数量。
 * @returns {string[]} 保持首次词形与输入顺序的唯一词项。
 */
export function splitAndDeduplicateTerms(value, maxItems = Number.POSITIVE_INFINITY) { // 文本入口始终先分隔，再委托共享去重逻辑。
  return deduplicateTerms(String(value || '').split(TERM_SEPARATOR), maxItems) // 保持旧搜索条件和关键词文本输入的分隔语义。
}
