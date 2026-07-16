import assert from 'node:assert/strict' // 使用 Node 内置严格断言固定工具栏组件边界。
import { readFile } from 'node:fs/promises' // 读取单文件组件源码而不引入额外 Vue 测试依赖。
import test from 'node:test' // 使用零依赖内置测试运行器声明用例。

test('PaperComparisonToolbar 仅通过 compare 和 clear 事件通知页面', async () => { // 验证展示组件不会越过页面状态层访问 API。
  const sourceUrl = new URL('../src/components/PaperComparisonToolbar.vue', import.meta.url) // 从测试文件稳定定位对应单文件组件。
  const source = await readFile(sourceUrl, 'utf8') // 读取组件源码用于验证公共边界。

  assert.match(source, /defineEmits\(\['compare', 'clear'\]\)/) // 验证工具栏只暴露两项页面操作事件。
  assert.match(source, /emit\('compare'\)/) // 验证比较按钮通过事件通知父页面。
  assert.match(source, /emit\('clear'\)/) // 验证清空按钮通过事件通知父页面。
  assert.doesNotMatch(source, /services\//) // 验证组件不直接导入或调用任何前端 API 服务。
})
