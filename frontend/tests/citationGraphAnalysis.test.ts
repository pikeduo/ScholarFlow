/** 验证事实型筛选和局部结构指标只使用当前搜索快照中的真实引用事实。 */

import assert from 'node:assert/strict' // 使用 Node 内置断言保持测试离线。
import test from 'node:test' // 使用轻量测试运行器覆盖纯函数边界。
import { analyzeCitationGraph, filterCitationGraphData, type CitationAnalysisNode } from '../src/utils/citationGraphAnalysis.ts' // 导入待验证的事实筛选和结构指标函数。
import type { CitationGraphData, VisualEdge } from '../src/utils/citationGraphLayout.ts' // 复用生产图数据类型。

const nodes: CitationAnalysisNode[] = [ // 构造固定的四节点真实引用图。
  { id: 'a', year: 2024 },
  { id: 'b', year: 2023 },
  { id: 'c', year: 2022 },
  { id: 'd', year: null },
]

const edges: VisualEdge[] = [ // A 引用 B 和 C，B 与 C 均引用 D。
  { sourceId: 'a', targetId: 'b', edgeType: 'cites' },
  { sourceId: 'a', targetId: 'c', edgeType: 'cites' },
  { sourceId: 'b', targetId: 'd', edgeType: 'cites' },
  { sourceId: 'c', targetId: 'd', edgeType: 'cites' },
  { sourceId: 'd', targetId: 'a', edgeType: 'same_work' },
]

test('事实型筛选移除不可见端点边且不修改输入响应', () => { // 验证年份、来源和局部度数筛选只影响前端图。
  const graph: CitationGraphData = { nodes: [{ paper_id: 'a', title: 'A', year: 2024, relevance: null, source: 'openalex' }, { paper_id: 'b', title: 'B', year: 2023, relevance: null, source: 'semantic_scholar' }, { paper_id: 'c', title: 'C', year: 2022, relevance: null, source: 'arxiv' }, { paper_id: 'd', title: 'D', year: null, relevance: null, source: 'openalex' }], edges: edges.map((edge) => ({ source_paper_id: edge.sourceId, target_paper_id: edge.targetId, edge_type: edge.edgeType })), truncated: false, max_nodes: 30 } // 构造兼容后端契约的数据。
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
