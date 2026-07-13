/** 将来源提供的 DOI 规范化为可安全在新标签页打开的 doi.org 链接。 */
export function buildDoiUrl(value) { // 接收可能为裸标识、doi: 前缀或完整解析器 URL 的来源字段。
  const rawValue = String(value || '').trim() // 统一空值和前后空白，避免无效链接进入 DOM。
  if (!rawValue) return null // 缺失 DOI 时不渲染无意义的空链接。
  const identifier = rawValue.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '').replace(/^doi:\s*/i, '').trim() // 去除常见 DOI 解析器 URL 或前缀以统一为标识本身。
  if (!/^10\.\d{4,9}\/\S+$/i.test(identifier)) return null // 只接受 DOI 规范的 10.<注册机构>/<后缀> 格式，拒绝任意外部 URL。
  return `https://doi.org/${encodeURIComponent(identifier)}` // 以固定可信解析器域名和编码后的标识构造链接。
}
