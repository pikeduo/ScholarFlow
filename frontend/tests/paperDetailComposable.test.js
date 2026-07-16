import assert from 'node:assert/strict' // 使用 Node 内置严格断言验证详情状态。
import test from 'node:test' // 使用零依赖内置测试运行器声明用例。
import { effectScope } from 'vue' // 在测试中提供与页面一致的组合式作用域。

import { usePaperDetail } from '../src/composables/usePaperDetail.js' // 导入待测的页面无关详情状态层。

function createDeferred() { // 构造可由测试精确控制完成时机的异步请求替身。
  let resolve // 保存成功回调供测试在任意时机触发。
  let reject // 保存失败回调供测试在任意时机触发。
  const promise = new Promise((nextResolve, nextReject) => { resolve = nextResolve; reject = nextReject }) // 创建等待测试显式结束的 Promise。
  return { promise, resolve, reject } // 暴露 Promise 及其成功、失败控制器。
}

function createDetailState(getPaperDetail) { // 在 Vue effect scope 中构造详情 Composable，避免脱离组件生命周期。
  const scope = effectScope() // 模拟页面组件拥有的响应式作用域。
  const state = scope.run(() => usePaperDetail({ getPaperDetail, getErrorMessage: (error) => `安全错误：${error.message}` })) // 注入无网络请求替身与确定性错误映射。
  return { scope, ...state } // 让每个用例能同时访问状态和卸载控制。
}

test('usePaperDetail 清除旧状态并忽略快速切换后的迟到响应', async () => { // 验证详情切换不会让旧论文覆盖当前抽屉。
  const first = createDeferred() // 构造第一篇论文的慢请求。
  const second = createDeferred() // 构造第二篇论文的慢请求。
  const requestedIds = [] // 记录 Composable 传给既有 API 的稳定标识。
  const state = createDetailState((paperId) => { requestedIds.push(paperId); return paperId === 'paper-1' ? first.promise : second.promise }) // 按论文标识返回各自受控请求。

  const firstRequest = state.openPaperDetail({ paper_id: 'paper-1' }) // 打开第一篇论文详情。
  assert.equal(state.detailLoading.value, true) // 验证有效请求立即进入加载状态。
  const secondRequest = state.openPaperDetail(' paper-2 ') // 在首个响应前快速切换为第二篇论文。
  assert.equal(state.detailPaper.value, null) // 验证切换时不会继续展示旧详情。
  first.resolve({ paper_id: 'paper-1', title: '过期论文' }) // 先返回已经过期的第一篇详情。
  await firstRequest // 等待第一条异步链路完整结束。
  assert.equal(state.detailPaper.value, null) // 验证迟到的第一篇详情被忽略。
  second.resolve({ paper_id: 'paper-2', title: '当前论文' }) // 返回当前第二篇详情。
  await secondRequest // 等待当前异步链路写入状态。

  assert.deepEqual(requestedIds, ['paper-1', 'paper-2']) // 验证对象与字符串输入都被规范化为稳定标识。
  assert.equal(state.detailPaper.value.title, '当前论文') // 验证只展示最新论文详情。
  assert.equal(state.detailLoading.value, false) // 验证最新请求完成后结束加载。
  state.scope.stop() // 清理测试作用域。
})

test('usePaperDetail 支持安全错误、关闭清理和卸载后忽略迟到响应', async () => { // 验证失败及页面离开时不会留下可见旧状态。
  const deferred = createDeferred() // 构造卸载前仍在进行的详情请求。
  const state = createDetailState(async (paperId) => { if (paperId === 'bad') throw new Error('服务不可用'); return deferred.promise }) // 按标识模拟失败或慢成功。

  await state.openPaperDetail('bad') // 请求会立即失败的详情。
  assert.equal(state.detailError.value, '安全错误：服务不可用') // 验证页面提供的错误映射得到保留。
  state.closePaperDetail() // 模拟用户关闭详情抽屉。
  assert.equal(state.detailError.value, '') // 验证关闭清除旧错误。
  const pendingRequest = state.openPaperDetail('paper-late') // 发起一个会在页面离开后才完成的请求。
  state.scope.stop() // 模拟页面卸载并使请求版本失效。
  deferred.resolve({ paper_id: 'paper-late', title: '不应写入' }) // 返回卸载后的迟到详情。
  await pendingRequest // 等待异步链路结束。

  assert.equal(state.detailPaper.value, null) // 验证卸载后迟到响应不会写入详情。
  assert.equal(state.detailLoading.value, false) // 验证关闭或卸载后不会遗留加载状态。
})
