import assert from 'node:assert/strict' // 使用 Node 内置严格断言验证词项规范化边界。
import test from 'node:test' // 使用零依赖内置测试运行器声明用例。

import { deduplicateTerms, splitAndDeduplicateTerms } from '../src/utils/terms.js' // 导入待测的共享纯函数。

test('splitAndDeduplicateTerms 支持中英文分隔符并保留首次词形与顺序', () => { // 验证搜索条件和文本关键词共用的自由文本语义。
  const terms = splitAndDeduplicateTerms(' Transformer, ETT，transformer\nbenchmark ') // 构造混合分隔符、空白和大小写重复输入。

  assert.deepEqual(terms, ['Transformer', 'ETT', 'benchmark']) // 验证分隔、清理和大小写无关去重均保持既有行为。
})

test('deduplicateTerms 不拆分数组元素并支持展示数量上限', () => { // 验证文献库数组关键词与论文卡片标签的不同业务边界。
  const terms = deduplicateTerms(['Method, Dataset', 'method, dataset', 'ETT', '', 'Benchmark'], 2) // 构造包含逗号元素、重复项和展示上限的数组。

  assert.deepEqual(terms, ['Method, Dataset', 'ETT']) // 验证数组元素不被二次拆分，且只保留前两个唯一可读词项。
  assert.deepEqual(deduplicateTerms(['ETT'], 0), []) // 验证零上限可安全用于主动隐藏标签的边界。
})
