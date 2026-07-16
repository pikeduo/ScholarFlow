import assert from 'node:assert/strict' // 使用 Node 内置严格断言验证比较状态。
import test from 'node:test' // 使用零依赖内置测试运行器声明用例。
import { effectScope } from 'vue' // 在测试中提供与页面一致的组合式作用域。

import { usePaperComparison } from '../src/composables/usePaperComparison.js' // 导入待测的页面无关比较状态层。

function createComparisonState(comparePapers) { // 在 Vue effect scope 中构造比较 Composable，避免脱离组件生命周期。
  const scope = effectScope() // 模拟页面组件拥有的响应式作用域。
  const state = scope.run(() => usePaperComparison({ comparePapers, getErrorMessage: (error) => `安全错误：${error.message}` })) // 注入无网络比较替身与确定性错误映射。
  return { scope, ...state } // 让每个用例能同时访问状态和卸载控制。
}

test('usePaperComparison 保持选择顺序、限制二至五篇并在选择变化后失效旧结果', async () => { // 验证两页共享的选择边界与显示状态。
  const requests = [] // 记录是否只在合法数量时调用注入 API。
  const state = createComparisonState(async (paperIds) => { requests.push(paperIds); return { items: paperIds.map((paper_id) => ({ paper_id })) } }) // 返回按请求顺序构造的事实型结果。

  await state.openPaperComparison() // 未选择论文时尝试比较。
  assert.equal(requests.length, 0) // 验证少于两篇时绝不发起 API 请求。
  assert.equal(state.comparisonError.value, '请至少选择 2 篇论文进行比较') // 验证最小选择提示稳定可展示。
  for (const paperId of ['paper-2', 'paper-1', 'paper-3', 'paper-4', 'paper-5']) state.togglePaperComparison(paperId) // 按用户点击顺序选满五篇。
  assert.deepEqual(state.comparisonPaperIds.value, ['paper-2', 'paper-1', 'paper-3', 'paper-4', 'paper-5']) // 验证 Composable 不排序或重排用户选择。
  assert.equal(state.isPaperSelectionDisabled('paper-6'), true) // 验证满额后禁用未选论文。
  assert.equal(state.isPaperSelectionDisabled('paper-1'), false) // 验证已选论文仍可取消选择。
  state.togglePaperComparison('paper-6') // 尝试加入第六篇论文。
  assert.equal(state.comparisonError.value, '一次最多比较 5 篇论文') // 验证前端上限提示稳定。
  await state.openPaperComparison() // 对合法选择执行比较。
  assert.deepEqual(requests, [['paper-2', 'paper-1', 'paper-3', 'paper-4', 'paper-5']]) // 验证请求只发送稳定 ID 且保持顺序。
  assert.equal(state.comparisonResult.value.items.length, 5) // 验证合法比较结果被保存。
  state.togglePaperComparison('paper-3') // 用户取消中间论文选择。
  assert.equal(state.comparisonResult.value, null) // 验证选择变化会清除不再可信的旧结果。
  assert.deepEqual(state.comparisonPaperIds.value, ['paper-2', 'paper-1', 'paper-4', 'paper-5']) // 验证删除不影响其余论文原始顺序。
  state.scope.stop() // 清理测试作用域。
})

test('usePaperComparison 处理请求失败、关闭保留选择、清空及删除同步', async () => { // 验证页面关闭和文献库删除的共享语义。
  let shouldFail = true // 控制首次比较失败、后续比较成功。
  const state = createComparisonState(async (paperIds) => { if (shouldFail) throw new Error('比较暂不可用'); return { items: paperIds } }) // 提供可切换的无网络比较替身。

  state.togglePaperComparison({ paper_id: 'paper-1' }) // 选择第一篇论文。
  state.togglePaperComparison({ paper_id: 'paper-2' }) // 选择第二篇论文以满足最小数量。
  await state.openPaperComparison() // 执行会失败的比较。
  assert.equal(state.comparisonError.value, '安全错误：比较暂不可用') // 验证保留页面提供的安全错误映射。
  shouldFail = false // 切换为成功响应。
  await state.openPaperComparison() // 再次比较得到结果。
  state.closePaperComparison() // 模拟用户关闭比较弹层。
  assert.deepEqual(state.comparisonPaperIds.value, ['paper-1', 'paper-2']) // 验证关闭仅关闭结果，不清除选择。
  assert.equal(state.comparisonResult.value, null) // 验证关闭不保留已关闭的结果。
  state.removePaperComparison('paper-1') // 模拟文献库删除一篇已选收藏。
  assert.deepEqual(state.comparisonPaperIds.value, ['paper-2']) // 验证删除收藏会同步移出比较选择。
  state.clearPaperComparison() // 模拟用户点击工具栏清空操作。
  assert.deepEqual(state.comparisonPaperIds.value, []) // 验证清空重置全部选择。
  assert.equal(state.comparisonError.value, '') // 验证清空移除旧错误。
  state.scope.stop() // 清理测试作用域。
})
