/** 验证时间分层引用图的纯布局规则，不依赖浏览器或 D3。 */

import assert from 'node:assert/strict' // 使用 Node 内置断言验证布局输出。
import test from 'node:test' // 使用 Node 内置测试运行器保持前端测试轻量。
import { buildCitationGraphLayout, type CitationGraphData } from '../src/utils/citationGraphLayout.ts' // 引入待测的纯布局函数和数据契约。

const graph: CitationGraphData = { // 构造两条引用分支、一个版本族和一篇孤立论文。
  nodes: [
    { paper_id: 'foundation', title: 'Foundation paper', year: 2020, relevance: 0.8, source: 'openalex', work_family_id: 'family-a' },
    { paper_id: 'foundation-preprint', title: 'Foundation preprint', year: 2019, relevance: 0.6, source: 'arxiv', work_family_id: 'family-a' },
    { paper_id: 'method', title: 'Method paper', year: 2022, relevance: 0.9, source: 'semantic_scholar' },
    { paper_id: 'application', title: 'Application paper', year: 2024, relevance: 0.7, source: 'openalex' },
    { paper_id: 'parallel-old', title: 'Parallel old paper', year: 2021, relevance: 0.5, source: 'openalex' },
    { paper_id: 'parallel-new', title: 'Parallel new paper', year: 2023, relevance: 0.5, source: 'openalex' },
    { paper_id: 'isolated', title: 'Isolated paper', year: 2025, relevance: 0.4, source: 'pubmed' },
  ],
  edges: [
    { source_paper_id: 'method', target_paper_id: 'foundation', edge_type: 'cites' },
    { source_paper_id: 'application', target_paper_id: 'method', edge_type: 'cites' },
    { source_paper_id: 'parallel-new', target_paper_id: 'parallel-old', edge_type: 'cites' },
    { source_paper_id: 'foundation', target_paper_id: 'foundation-preprint', edge_type: 'same_work' },
  ],
  truncated: false,
  max_nodes: 30,
}

function layout(overrides: Partial<Parameters<typeof buildCitationGraphLayout>[1]> = {}) { // 为各测试提供统一的默认布局参数。
  return buildCitationGraphLayout(graph, { width: 960, collapseFamilies: true, includeVersionLinks: false, includeIsolates: false, ...overrides }) // 保持默认的版本族合并和孤立节点折叠策略。
}

test('按发表年份固定横轴，并将引用分支拆分布局', () => { // 验证核心时间分层与弱连通分量行为。
  const result = layout() // 计算默认主图布局。
  const byId = new Map(result.nodes.map((node) => [node.id, node])) // 建立便于断言的节点索引。
  const foundation = byId.get('family:family-a') // 读取已合并版本族的代表节点。
  const method = byId.get('paper:method') // 读取中间方法论文。
  const application = byId.get('paper:application') // 读取最新应用论文。

  assert.ok(foundation && method && application) // 确认三个主分支节点均可见。
  assert.ok(foundation.x < method.x && method.x < application.x) // 验证旧论文在左、新论文在右。
  assert.equal(result.componentCount, 2) // 验证两个互不连接的引用分支分开统计。
  assert.equal(result.isolatedCount, 1) // 验证孤立论文被识别而非混入主图。
  assert.equal(byId.has('paper:isolated'), false) // 验证默认折叠孤立论文。
})

test('版本族可默认合并，孤立论文可按需展开为网格', () => { // 验证版本族和孤立论文的可控呈现。
  const result = layout({ includeIsolates: true }) // 显式展开孤立论文。
  const family = result.nodes.find((node) => node.id === 'family:family-a') // 读取合并后的版本族节点。
  const isolated = result.nodes.find((node) => node.id === 'paper:isolated') // 读取展开后的孤立论文。

  assert.equal(family?.memberCount, 2) // 验证同一版本族仅显示为一个工作节点。
  assert.equal(result.edges.some((edge) => edge.edgeType === 'same_work'), false) // 验证合并模式不会重复绘制版本族边。
  assert.equal(isolated?.isIsolate, true) // 验证展开项仍保留孤立语义。
})

test('一阶邻域只保留选中论文的直接引用关系', () => { // 验证交互模式不会泄漏无关分支。
  const result = layout({ focusNodeId: 'method' }) // 仅查看方法论文的一阶邻域。
  const ids = new Set(result.nodes.map((node) => node.id)) // 收集可见节点标识。

  assert.deepEqual(ids, new Set(['family:family-a', 'paper:method', 'paper:application'])) // 验证只保留中心、引用它和被它引用的论文。
  assert.equal(result.edges.length, 2) // 验证边也同步收缩为直接关系。
})
