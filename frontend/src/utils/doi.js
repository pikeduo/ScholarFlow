/** 将来源提供的 DOI 规范化为可安全在新标签页打开的 doi.org 链接。 */
export function buildDoiUrl(value) { // 接收可能为裸标识、doi: 前缀或完整解析器 URL 的来源字段。
  const rawValue = String(value || '').trim() // 统一空值和前后空白，避免无效链接进入 DOM。
  if (!rawValue) return null // 缺失 DOI 时不渲染无意义的空链接。
  const identifier = rawValue.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '').replace(/^doi:\s*/i, '').trim() // 去除常见 DOI 解析器 URL 或前缀以统一为标识本身。
  if (!/^10\.\d{4,9}\/\S+$/i.test(identifier)) return null // 只接受 DOI 规范的 10.<注册机构>/<后缀> 格式，拒绝任意外部 URL。
  return `https://doi.org/${encodeURIComponent(identifier)}` // 以固定可信解析器域名和编码后的标识构造链接。
}

/** 仅接受来源明确标识为 PDF 的 HTTP(S) 开放访问链接。 */
export function buildPublicPdfUrl(value) { // 接收来源返回的开放访问 URL，而不是用户输入的任意网页地址。
  const rawValue = String(value || '').trim() // 统一空值和前后空白，避免解析异常进入界面。
  if (!rawValue) return null // 缺失开放访问地址时不显示 PDF 按钮。
  try { // 通过标准 URL 解析器校验协议和路径。
    const url = new URL(rawValue) // 解析来源返回的候选公开地址。
    const path = decodeURIComponent(url.pathname).toLowerCase() // 解码并规范化路径以识别常见 PDF 形式。
    if (!['http:', 'https:'].includes(url.protocol) || (!path.endsWith('.pdf') && !path.includes('/pdf/'))) return null // 仅允许 HTTP(S) 且路径明确指向 PDF 的链接。
    return url.href // 返回浏览器已规范化的安全公开 PDF 链接。
  } catch { return null } // 地址无效或解码失败时隐藏按钮而不是渲染危险链接。
}
