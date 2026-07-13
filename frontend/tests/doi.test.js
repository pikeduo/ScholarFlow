import assert from 'node:assert/strict' // 使用 Node 内置严格断言验证 DOI 链接安全边界。
import test from 'node:test' // 使用零依赖测试运行器声明用例。

import { buildDoiUrl, buildPublicPdfUrl } from '../src/utils/doi.js' // 导入待测 DOI 和公开 PDF 链接构造函数。

test('buildDoiUrl 仅将合法 DOI 规范化为 doi.org 新标签链接', () => { // 覆盖裸 DOI、常见前缀、完整 URL 和危险输入。
  assert.equal(buildDoiUrl('10.1000/example'), 'https://doi.org/10.1000%2Fexample') // 验证裸 DOI 会使用固定解析器域名和编码路径。
  assert.equal(buildDoiUrl('doi:10.5555/ABC-123'), 'https://doi.org/10.5555%2FABC-123') // 验证 doi: 前缀不会重复进入链接。
  assert.equal(buildDoiUrl('https://doi.org/10.1000/example'), 'https://doi.org/10.1000%2Fexample') // 验证已有 DOI 解析器 URL 会被统一规范化。
  assert.equal(buildDoiUrl('javascript:alert(1)'), null) // 验证任意脚本协议不会成为链接。
  assert.equal(buildDoiUrl(''), null) // 验证空值不渲染 DOI 链接。
})

test('buildPublicPdfUrl 只接受来源明确提供的 HTTP(S) PDF 链接', () => { // 覆盖常见 PDF 后缀、arXiv 路径、普通网页和危险协议。
  assert.equal(buildPublicPdfUrl('https://example.org/paper.pdf'), 'https://example.org/paper.pdf') // 验证标准 PDF 文件链接可显示公开 PDF 按钮。
  assert.equal(buildPublicPdfUrl('https://arxiv.org/pdf/2501.00001v2'), 'https://arxiv.org/pdf/2501.00001v2') // 验证 arXiv 无扩展名的标准 PDF 路径可显示按钮。
  assert.equal(buildPublicPdfUrl('https://example.org/article'), null) // 验证普通落地页不会被伪装为 PDF 下载。
  assert.equal(buildPublicPdfUrl('javascript:alert(1)'), null) // 验证危险协议不会成为公开 PDF 链接。
})
