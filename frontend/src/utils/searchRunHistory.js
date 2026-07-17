/** 用于搜索运行历史的纯状态变换，避免页面在过期记录场景保留伪装为最新的数据。 */

/** 以服务端最新历史完整替换页面的本地索引。 */
export function replaceSearchRunHistory(items) { // 接收已由 API 客户端完成基本契约校验的历史项数组。
  return Array.isArray(items) ? [...items] : [] // 创建新数组触发 Vue 更新；异常输入安全清空而不保留旧状态。
}

/** 从当前历史索引移除指定运行，供恢复或清理接口返回 404 时复用。 */
export function removeSearchRunHistoryItem(items, runId) { // 只处理稳定运行标识，不依赖论文或查询正文。
  const normalizedRunId = String(runId || '').trim() // 规范化 URL、恢复接口或删除接口提供的运行标识。
  if (!normalizedRunId) return Array.isArray(items) ? [...items] : [] // 无效标识不应误删任何可见条目。
  return (Array.isArray(items) ? items : []).filter((item) => item?.run_id !== normalizedRunId) // 仅移除已确认不存在的本地索引项。
}
