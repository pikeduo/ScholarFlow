/** 将毫秒总耗时格式化为自动进位的中文可读时长。 */
export function formatDuration(milliseconds) { // 仅负责展示转换，不修改后端保存的精确毫秒数。
  const value = Number(milliseconds) // 兼容 API JSON 数字和可转换的调用方输入。
  if (!Number.isFinite(value) || value <= 0) return '0 ms' // 缺失、非法或零耗时统一显示稳定零值。
  if (value < 1000) return `${Math.round(value)} ms` // 未满一秒时保留毫秒精度。
  if (value < 60_000) return `${formatDecimal(value / 1000)} 秒` // 满一秒后自动进位为秒。
  return `${formatDecimal(value / 60_000)} 分钟` // 满一分钟后自动进位为分钟。
}

/** 删除小数末尾无意义的零，避免展示“1.0 秒”。 */
function formatDecimal(value) { // 接收已经换算为秒或分钟的有限正数。
  return value.toFixed(1).replace(/\.0$/, '') // 固定保留一位小数后去除整数末尾的零。
}
