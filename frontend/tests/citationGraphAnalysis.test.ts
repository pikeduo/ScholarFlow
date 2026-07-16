/** 验证引用路径、上下游、筛选和局部结构指标均只使用当前快照中的真实引用事实。 */

import assert from 'node:assert/strict' // 使用 Node 内置断言保持测试离线。
import test from 'node:test' // 使用轻量测试运行器覆盖纯函数边界。
import { analyzeCitationGraph, collectCitationAncestors, collectCitationDescendants, filterCitationGraphData, findCitationPaths, type CitationAnalysisNode } from '../src/utils/citationGraphAnalysis.ts' // 导入待验证的事实型图分析函数。
import type { CitationGraphData, VisualEdge } from '../src/utils/citationGraphLayout.ts' // 复用生产图数据类型。

const nodes: CitationAnalysisNode[] = [ // 构造固定的四节点真实引用图。
  { id: 'a', title: 'A', year: 2024, source: 'openalex' },
  { id: 'b', title: 'B', year: 2023, source: 'semantic_scholar' },
  { id: 'c', title: 'C', year: 2022, source: 'arxiv' },
  { id: 'd', title: 'D', year: null, source: 'openalex' },
]

const edges: VisualEdge[] = [ // A 引用 B 和 C，B 与 C 均引用 D。
  { sourceId: 'a', targetId: 'b', edgeType: 'cites' },
  { sourceId: 'a', targetId: 'c', edgeType: 'cites' },
  { sourceId: 'b', targetId: 'd', edgeType: 'cites' },
  { sourceId: 'c', targetId: 'd', edgeType: 'cites' },
  { sourceId: 'd', targetId: 'a', edgeType: 'same_work' },
]

test('路径查询优先返回稳定的最短真实引用路径，并忽略版本族边', () => { // 验证两条最短路径按稳定节点标识返回。
  const result = findCitationPaths(nodes, edges, 'a', 'd', { directed: true, maxDepth: 4, maxPaths: 3 }) // 沿真实引用方向寻找 A 到 D。
  assert.deepEqual(result.paths.map((path) => path.nodeIds), [['a', 'b', 'd'], ['a', 'c', 'd']]) // 验证两条两跳最短路径均被保留。
  assert.ok(result.paths.every((path) => path.edgeIds.every((edgeId) => edgeId.startsWith('cites:')))) // same_work 不得参与引用路径。
})

test('路径查询安全处理环、深度、端点异常和无向结构探索', () => { // 验证受控 BFS 不会无限递归或反转事实边。
  const cyclicEdges = [...edges, { sourceId: 'd', targetId: 'a', edgeType: 'cites' as const }] // 添加真实循环引用关系。
  assert.equal(findCitationPaths(nodes, cyclicEdges, 'a', 'd', { directed: true, maxDepth: 1, maxPaths: 3 }).paths.length, 0) // 深度不足时不应返回路径。
  assert.deepEqual(findCitationPaths(nodes, cyclicEdges, 'a', 'a', { directed: true, maxDepth: 4, maxPaths: 3 }).paths[0].nodeIds, ['a']) // 相同端点返回零长度受控路径。
  assert.equal(findCitationPaths(nodes, cyclicEdges, 'missing', 'a', { directed: true, maxDepth: 4, maxPaths: 3 }).paths.length, 0) // 图外端点返回明确空结果。
  assert.equal(findCitationPaths(nodes, edges, 'd', 'a', { directed: false, maxDepth: 3, maxPaths: 3 }).paths.length, 2) // 无向模式仅允许结构探索，不改写边事实。
})

test('前置工作沿出边展开，后续引用沿入边展开且节点只保留最短层级', () => { // 验证上下游方向语义与 A → B 引用定义一致。
  const ancestors = collectCitationAncestors('a', nodes, edges, 3) // A 的前置工作是 B、C 和 D。
  const descendants = collectCitationDescendants('d', nodes, edges, 3) // D 的后续引用论文是 B、C 和 A。
  assert.deepEqual(ancestors.levels, [{ depth: 1, nodeIds: ['b', 'c'] }, { depth: 2, nodeIds: ['d'] }]) // 验证前置工作沿 outgoing 方向。
  assert.deepEqual(descendants.levels, [{ depth: 1, nodeIds: ['b', 'c'] }, { depth: 2, nodeIds: ['a'] }]) // 验证后续论文沿 incoming 方向。
})

test('事实型筛选移除不可见端点边且不修改输入响应', () => { // 验证年份、来源和局部度数筛选只影响前端图。
  const graph: CitationGraphData = { nodes: nodes.map((node) => ({ paper_id: node.id, title: node.title || node.id, year: node.year || null, relevance: null, source: node.source || 'unknown' })), edges: edges.map((edge) => ({ source_paper_id: edge.sourceId, target_paper_id: edge.targetId, edge_type: edge.edgeType })), truncated: false, max_nodes: 30 } // 构造兼容后端契约的数据。
  const snapshot = JSON.stringify(graph) // 保存输入快照验证纯函数不修改数据。
  const result = filterCitationGraphData(graph, { yearStart: 2023, sources: ['openalex', 'semantic_scholar'], minimumInDegree: 0 }) // 过滤较早年份和 arXiv 节点。
  assert.deepEqual(result.graph.nodes.map((node) => node.paper_id), ['a', 'b']) // 验证筛选仅保留满足条件的节点。
  assert.deepEqual(result.graph.edges.map((edge) => `${edge.source_paper_id}:${edge.target_paper_id}`), ['a:b']) // 验证端点不可见的边同步移除。
  assert.equal(JSON.stringify(graph), snapshot) // 验证筛选不会修改输入响应。
})

test('局部结构指标不将当前结果集关系解释为全局引用影响力', () => { // 验证指标仅统计当前给定节点和真实 cites。
  const metrics = analyzeCitationGraph(nodes, edges) // 计算当前小图的内部结构。
  assert.equal(metrics.nodeCount, 4) // 验证节点数量。
  assert.equal(metrics.citationEdgeCount, 4) // 验证 same_work 不计入引用边数。
  assert.deepEqual(metrics.maxInDegreeNodeIds, ['d']) // D 在当前结果集内部入度最高。
  assert.deepEqual(metrics.maxOutDegreeNodeIds, ['a']) // A 在当前结果集内部出度最高。
  assert.equal(metrics.earliestYear, 2022) // 验证缺失年份不干扰时间跨度。
  assert.equal(metrics.latestYear, 2024) // 验证最新可信年份。
})
