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

test('首末年份标题向时间轴外侧放置，跨年份边连接左右圆周端口', () => { // 验证标题和引用边分别占据外侧与内部区域。
  const result = layout() // 计算包含最早、最晚和中间年份的默认布局。
  const source = result.nodes.find((node) => node.id === 'paper:method') // 读取较新引用方节点。
  const target = result.nodes.find((node) => node.id === 'family:family-a') // 读取较早被引目标节点。
  const edge = result.edges.find((item) => item.id === 'cites:paper:method:family:family-a') // 定位跨年份真实引用边。
  const pathNumbers = edge?.path.match(/-?\d+\.\d+/g)?.map(Number) || [] // 提取三次贝塞尔的两端和控制点坐标。

  assert.equal(target?.labelSide, 'left') // 验证最左年份标题向左侧外置。
  assert.equal(result.nodes.find((node) => node.id === 'paper:application')?.labelSide, 'right') // 验证最右年份标题向右侧外置。
  assert.ok(source && target && edge) // 确认跨年份边和端点节点均存在。
  assert.equal(pathNumbers.length, 8) // 验证跨年份边使用平缓的三次贝塞尔曲线。
  assert.ok(pathNumbers[0] < source.x) // 验证来源于源节点面向目标的左侧圆周端口，而不是圆心。
  assert.ok(pathNumbers[6] > target.x + target.radius) // 验证箭头尖端停在目标圆周外并预留间距。
})

test('同年真实引用边使用年份线附近的独立弧线轨道', () => { // 验证同年引用关系不与时间线或其他同年边重合。
  const sameYearGraph: CitationGraphData = { // 构造两个同年且存在真实引用关系的最小图。
    nodes: [ // 两个节点会被布局到同一条年份时间列。
      { paper_id: 'same-year-a', title: 'Same year source', year: 2023, relevance: 0.8, source: 'openalex' }, // 声明引用边起点。
      { paper_id: 'same-year-b', title: 'Same year target', year: 2023, relevance: 0.8, source: 'openalex' }, // 声明引用边终点。
    ],
    edges: [{ source_paper_id: 'same-year-a', target_paper_id: 'same-year-b', edge_type: 'cites' }], // 仅保留一条可审计的同年真实引用边。
    truncated: false, // 声明未发生后端节点裁剪。
    max_nodes: 30, // 保持与生产默认上限一致。
  }
  const result = buildCitationGraphLayout(sameYearGraph, { width: 960, collapseFamilies: true, includeVersionLinks: false, includeIsolates: false }) // 计算不依赖浏览器的稳定布局。
  const edge = result.edges[0] // 读取唯一边的 SVG 三次曲线路径。
  const source = result.nodes.find((node) => node.id === 'paper:same-year-a') // 读取路径起点节点坐标。
  const target = result.nodes.find((node) => node.id === 'paper:same-year-b') // 读取路径终点节点坐标。
  const pathNumbers = edge?.path.match(/-?\d+\.\d+/g)?.map(Number) || [] // 按路径格式提取起点、控制点和终点坐标。

  assert.ok(source && target && edge) // 确认同年节点和真实引用边均进入当前主图。
  assert.equal(pathNumbers.length, 8) // 验证真实引用边使用八个数值组成的三次贝塞尔曲线。
  assert.notEqual(pathNumbers[0], source.x) // 验证路径从节点朝内部的左右圆周端口离开。
  assert.equal(pathNumbers[1], source.y) // 验证端口保持在节点圆周水平中线。
  assert.notEqual(pathNumbers[2], source.x) // 验证控制点进入年份线附近的独立轨道。
  assert.ok(Math.abs(pathNumbers[6] - target.x) > target.radius) // 验证箭头终点停在目标圆周外而不是节点中心。
})

test('多条同年真实引用边分配不同的独立轨道', () => { // 验证同一年份的多条边不会重叠为一条弧线。
  const sameYearGraph: CitationGraphData = { // 构造三篇同年论文与两条真实引用边。
    nodes: [ // 三个节点会处于同一条年份线。
      { paper_id: 'same-year-a', title: 'Same year A', year: 2023, relevance: 0.8, source: 'openalex' }, // 声明第一条边的来源节点。
      { paper_id: 'same-year-b', title: 'Same year B', year: 2023, relevance: 0.8, source: 'openalex' }, // 声明两个边共享的目标节点。
      { paper_id: 'same-year-c', title: 'Same year C', year: 2023, relevance: 0.8, source: 'openalex' }, // 声明第二条边的来源节点。
    ],
    edges: [ // 保留两条同年真实引用关系。
      { source_paper_id: 'same-year-a', target_paper_id: 'same-year-b', edge_type: 'cites' }, // 第一条同年边。
      { source_paper_id: 'same-year-c', target_paper_id: 'same-year-b', edge_type: 'cites' }, // 第二条同年边。
    ],
    truncated: false, // 声明未发生后端节点裁剪。
    max_nodes: 30, // 保持与生产默认上限一致。
  }
  const result = buildCitationGraphLayout(sameYearGraph, { width: 960, collapseFamilies: true, includeVersionLinks: false, includeIsolates: false }) // 计算不依赖浏览器的稳定布局。
  const controlXs = result.edges.map((edge) => edge.path.match(/-?\d+\.\d+/g)?.map(Number)?.[2]) // 读取每条路径第一个控制点的横坐标。

  assert.equal(result.edges.length, 2) // 确认两条同年边均被保留。
  assert.equal(new Set(controlXs).size, 2) // 验证两条边分配到不同的年份线附近轨道。
})
