import assert from 'node:assert/strict' // 使用 Node 内置断言验证不依赖浏览器的历史自愈状态变换。
import test from 'node:test' // 使用项目既有 Node 测试运行器声明离线用例。

import { removeSearchRunHistoryItem, replaceSearchRunHistory } from '../src/utils/searchRunHistory.js' // 导入搜索页实际复用的纯历史状态函数。


test('恢复 404 时移除过期历史记录而不影响其他运行', () => { // 验证页面收到恢复接口 404 后可以安全自愈本地列表。
  const previous = [{ run_id: 'missing-run', status: 'failed' }, { run_id: 'kept-run', status: 'completed' }] // 构造包含一个过期条目和一个有效条目的本地索引。

  const next = removeSearchRunHistoryItem(previous, 'missing-run') // 模拟恢复指定运行返回 404 后的页面状态更新。

  assert.deepEqual(next, [{ run_id: 'kept-run', status: 'completed' }]) // 验证过期项被移除且其他运行不受影响。
})


test('删除 404 时同样移除过期历史记录且不要求重试', () => { // 验证 DELETE 资源已不存在时可以收敛本地条目。
  const previous = [{ run_id: 'deleted-elsewhere', status: 'failed' }] // 构造另一个页面已经清理后的旧本地索引。

  const next = removeSearchRunHistoryItem(previous, 'deleted-elsewhere') // 模拟 DELETE 返回 404 后的本地自愈。

  assert.deepEqual(next, []) // 验证页面不再保留会引导用户重复清理的过期条目。
})


test('历史读取成功时完整替换旧列表，读取失败路径可安全清空旧列表', () => { // 验证页面不会将旧索引伪装为当前数据库状态。
  const previous = [{ run_id: 'old-run', status: 'running' }] // 构造上一次读取成功后遗留在页面内存中的旧索引。
  const latest = [{ run_id: 'latest-run', status: 'failed' }] // 构造当前服务端实际返回的最新历史。

  assert.deepEqual(replaceSearchRunHistory(latest), latest) // 验证成功读取使用服务端完整列表而不是合并旧列表。
  assert.deepEqual(replaceSearchRunHistory(null), []) // 验证读取失败时页面可显式清空旧列表而非伪装为最新状态。
  assert.deepEqual(previous, [{ run_id: 'old-run', status: 'running' }]) // 验证纯函数不会隐式修改此前状态，便于页面明确替换。
})
