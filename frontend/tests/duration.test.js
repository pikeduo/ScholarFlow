import assert from 'node:assert/strict' // 使用 Node 内置严格断言验证纯展示逻辑。
import test from 'node:test' // 使用零依赖测试运行器声明用例。

import { formatDuration } from '../src/utils/duration.js' // 导入待测的耗时单位自动进位函数。

test('formatDuration 按毫秒、秒和分钟自动进位', () => { // 覆盖三个展示区间和小数收敛边界。
  assert.equal(formatDuration(0), '0 ms') // 验证零耗时保持毫秒单位。
  assert.equal(formatDuration(999), '999 ms') // 验证未满一秒不提前进位。
  assert.equal(formatDuration(1_480), '1.5 秒') // 验证秒级展示保留一位有效小数。
  assert.equal(formatDuration(60_000), '1 分钟') // 验证整分钟去除无意义小数。
  assert.equal(formatDuration(90_500), '1.5 分钟') // 验证分钟级展示自动进位并四舍五入。
})
