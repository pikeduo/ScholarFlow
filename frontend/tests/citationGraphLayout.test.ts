/** 验证研究主干筛选、平滑曲线路径与既有时间分层交互所依赖的纯函数。 */

import assert from 'node:assert/strict' // 使用 Node 内置断言保持测试不依赖浏览器或真实后端。
import test from 'node:test' // 使用 Node 内置测试运行器执行轻量纯函数用例。
import { assignYearColumns, buildCitationGraphLayout, selectBackboneEdges, type CitationGraphData, type VisualEdge, type VisualSeed } from '../src/utils/citationGraphLayout.ts' // 导入受测布局契约和主干筛选函数。

function graph(nodes: CitationGraphData['nodes'], edges: CitationGraphData['edges']): CitationGraphData { // 快速构造与后端兼容的受限引用图响应。
  return { nodes, edges, truncated: false, max_nodes: 30 } // 保持生产响应所需字段完整且不访问网络。
}

function seeds(...ids: string[]): VisualSeed[] { // 构造仅用于主干纯函数测试的稳定视觉节点。
  return ids.map((id, index) => ({ id: `paper:${id}`, paperIds: [id], title: id, year: 2020 + index, source: 'openalex', relevance: index / 10, familyId: null, memberCount: 1 })) // 复用已有可审计字段并固定年份与相关性。
}

function citation(sourceId: string, targetId: string): VisualEdge { // 构造一条真实引用视觉边。
  return { sourceId: `paper:${sourceId}`, targetId: `paper:${targetId}`, edgeType: 'cites' } // 保持引用方向与后端事实一致。
}

function layout(data: CitationGraphData, options: Partial<Parameters<typeof buildCitationGraphLayout>[1]> = {}) { // 为布局测试提供默认研究主干选项。
  return buildCitationGraphLayout(data, { width: 960, collapseFamilies: true, includeVersionLinks: false, includeIsolates: false, ...options }) // 允许个别用例覆盖视图或视觉上限。
}

const transitiveGraph = graph( // 构造 A → B → C 且 A → C 的可传递引用关系。
  [
    { paper_id: 'a', title: 'A', year: 2020, relevance: 0.6, source: 'openalex' },
    { paper_id: 'b', title: 'B', year: 2021, relevance: 0.7, source: 'openalex' },
    { paper_id: 'c', title: 'C', year: 2022, relevance: 0.8, source: 'openalex' },
  ],
  [
    { source_paper_id: 'a', target_paper_id: 'b', edge_type: 'cites' },
    { source_paper_id: 'b', target_paper_id: 'c', edge_type: 'cites' },
    { source_paper_id: 'a', target_paper_id: 'c', edge_type: 'cites' },
  ],
)

test('实际出现年份采用等间距序数列，缺失年份不产生空白', () => { // 验证时间轴横坐标仍只由可见年份决定。
  const columns = assignYearColumns([2018, 2021, 2029], 900) // 传入间隔不均匀的三个年份。
  const first = columns.get(2018) || 0 // 读取第一列坐标。
  const middle = columns.get(2021) || 0 // 读取中间列坐标。
  const last = columns.get(2029) || 0 // 读取最后一列坐标。
  assert.equal(middle - first, last - middle) // 验证年份差值不会改变列间距。
})

test('空图和单条边保持安全且不生成关系', () => { // 验证主干选择不对空输入或单条事实边作额外推断。
  assert.deepEqual(selectBackboneEdges([], []), { visibleEdges: [], hiddenEdgeCount: 0 }) // 空图应直接返回空结果。
  const only = citation('a', 'b') // 构造唯一真实引用边。
  const result = selectBackboneEdges(seeds('a', 'b'), [only]) // 执行主干筛选。
  assert.deepEqual(result.visibleEdges, [only]) // 唯一事实边必须被保留。
  assert.equal(result.hiddenEdgeCount, 0) // 单边不应被错误隐藏。
})

test('研究主干隐藏可传递边，而完整网络恢复全部事实边', () => { // 验证两个视图的核心事实边界。
  const backbone = layout(transitiveGraph) // 缺省视图必须是研究主干。
  const full = layout(transitiveGraph, { viewMode: 'full' }) // 显式切换到完整网络。
  assert.equal(backbone.visibleCitationEdgeCount, 2) // A → C 可由 A → B → C 表达，应在主干中隐藏。
  assert.equal(backbone.hiddenCitationEdgeCount, 1) // 主干统计必须准确回显被隐藏关系数量。
  assert.equal(full.visibleCitationEdgeCount, 3) // 完整网络必须恢复后端返回的全部真实引用事实。
  assert.ok(full.edges.some((edge) => edge.id === 'cites:paper:a:paper:c')) // 验证完整网络没有丢失直接引用。
})

test('重复边被稳定去重，输入数组不被主干筛选原地修改', () => { // 验证异常重复输入不会形成多条显示关系。
  const edges = [citation('a', 'b'), citation('a', 'b'), citation('b', 'c')] // 构造含重复事实边的输入数组。
  const snapshot = edges.map((edge) => ({ ...edge })) // 保存输入快照，验证函数不修改调用方数据。
  const first = selectBackboneEdges(seeds('a', 'b', 'c'), edges) // 第一次执行主干筛选。
  const second = selectBackboneEdges(seeds('a', 'b', 'c'), [...edges].reverse()) // 改变输入顺序后再次执行。
  assert.deepEqual(edges, snapshot) // 验证输入边数组及对象没有被原地改写。
  assert.equal(first.visibleEdges.filter((edge) => edge.sourceId === 'paper:a' && edge.targetId === 'paper:b').length, 1) // 验证重复边只显示一次。
  assert.deepEqual(first, second) // 验证相同事实集合与输入顺序无关。
})

test('循环关系安全结束且强连通分量内部边不会被全部约简', () => { // 验证异常循环不会无限递归或误删整个分量。
  const edges = [citation('a', 'b'), citation('b', 'c'), citation('c', 'a')] // 构造最小三节点强连通分量。
  const result = selectBackboneEdges(seeds('a', 'b', 'c'), edges) // 执行循环安全的主干筛选。
  assert.equal(result.visibleEdges.length, 3) // 分量内部边应保守保留。
  assert.equal(result.hiddenEdgeCount, 0) // 循环本身不能触发错误的传递约简。
})

test('版本族边不参与真实引用的主干筛选', () => { // 验证 same_work 不会被误当作可传递引用。
  const sameWork: VisualEdge = { sourceId: 'paper:a', targetId: 'paper:c', edgeType: 'same_work' } // 构造版本族辅助关系。
  const result = selectBackboneEdges(seeds('a', 'b', 'c'), [citation('a', 'b'), citation('b', 'c'), citation('a', 'c'), sameWork]) // 混合输入只应筛选 cites。
  assert.ok(result.visibleEdges.every((edge) => edge.edgeType === 'cites')) // 输出中不得混入版本族关系。
  assert.equal(result.hiddenEdgeCount, 1) // 真实的 A → C 仍可依据真实引用路径被隐藏。
})

test('单节点与全图上限生效，选中论文的直接关系优先保留', () => { // 验证视觉裁剪上限不会让当前选择论文失去所有直接关系。
  const edges = [citation('a', 'b'), citation('a', 'c'), citation('a', 'd'), citation('a', 'e'), citation('b', 'd')] // 构造出边密集的事实图。
  const capped = selectBackboneEdges(seeds('a', 'b', 'c', 'd', 'e'), edges, { maxOutgoingEdgesPerNode: 2, maxVisibleEdges: 2 }) // 应用普通节点和全图上限。
  const focused = selectBackboneEdges(seeds('a', 'b', 'c', 'd', 'e'), edges, { priorityNodeId: 'paper:a', maxOutgoingEdgesPerNode: 1, maxVisibleEdges: 1 }) // 将 A 作为当前选中论文。
  assert.ok(capped.visibleEdges.length <= 2) // 验证全图上限生效。
  assert.ok(capped.visibleEdges.filter((edge) => edge.sourceId === 'paper:a').length <= 2) // 验证单节点出边上限生效。
  assert.equal(focused.visibleEdges.filter((edge) => edge.sourceId === 'paper:a').length, 4) // 选中论文直接事实关系可突破普通上限，避免侧栏丢失关系。
})

test('曲线路径使用三次贝塞尔，端点位于节点圆周之外且结果稳定', () => { // 验证新默认路径不再依赖正交回折。
  const first = layout(transitiveGraph, { viewMode: 'full' }) // 计算完整网络平滑曲线路径。
  const second = layout(transitiveGraph, { viewMode: 'full' }) // 使用相同输入再次计算。
  const edge = first.edges.find((item) => item.id === 'cites:paper:a:paper:b') // 定位一条跨年引用边。
  const source = first.nodes.find((node) => node.id === 'paper:a') // 读取路径起点节点。
  const target = first.nodes.find((node) => node.id === 'paper:b') // 读取路径终点节点。
  assert.ok(edge && source && target) // 确认所需节点和路径均已生成。
  assert.match(edge.path, / C /) // 验证路径使用 SVG 三次贝塞尔命令。
  assert.equal(edge.points.length, 4) // 验证路径保留起点、两控制点和终点。
  assert.ok(Math.hypot(edge.points[0].x - source.x, edge.points[0].y - source.y) > source.radius) // 验证曲线从源节点圆周外开始。
  assert.ok(Math.hypot((edge.points.at(-1)?.x || 0) - target.x, (edge.points.at(-1)?.y || 0) - target.y) > target.radius) // 验证曲线在目标节点圆周外结束。
  assert.ok(edge.points.every((point) => Number.isFinite(point.x) && Number.isFinite(point.y))) // 验证路径不产生 NaN 或无限值。
  assert.deepEqual(first.edges, second.edges) // 验证刷新相同输入不会改变曲线结果。
})

test('同年关系获得稳定独立弧线，常驻标签受全图上限约束', () => { // 验证同年边不会退化为重叠直线，标签不会重新铺满画布。
  const data = graph( // 构造同年引用和足够多的节点。
    Array.from({ length: 10 }, (_, index) => ({ paper_id: `p${index}`, title: `Paper ${index}`, year: index < 4 ? 2023 : 2020 + index, relevance: index / 10, source: 'openalex' })), // 保持前四篇论文同年。
    [
      { source_paper_id: 'p0', target_paper_id: 'p1', edge_type: 'cites' },
      { source_paper_id: 'p2', target_paper_id: 'p1', edge_type: 'cites' },
      { source_paper_id: 'p3', target_paper_id: 'p1', edge_type: 'cites' },
      ...Array.from({ length: 6 }, (_, index) => ({ source_paper_id: `p${index + 4}`, target_paper_id: 'p1', edge_type: 'cites' as const })),
    ],
  )
  const result = layout(data, { viewMode: 'full' }) // 完整网络应仍使用同一套平滑曲线路径。
  const sameYearPaths = result.edges.filter((edge) => ['cites:paper:p0:paper:p1', 'cites:paper:p2:paper:p1', 'cites:paper:p3:paper:p1'].includes(edge.id)).map((edge) => edge.path) // 收集同年边路径。
  assert.equal(new Set(sameYearPaths).size, 3) // 验证同年关系取得不同但稳定的曲线路径。
  assert.ok(result.nodes.filter((node) => node.showLabel).length <= 8) // 验证默认常驻标签不超过全图上限。
})

test('一阶邻域、版本族合并与孤立论文策略继续保留', () => { // 验证主干改造不改变既有节点范围交互。
  const data = graph( // 构造版本族、引用链和孤立论文。
    [
      { paper_id: 'old', title: 'Old', year: 2019, relevance: 0.8, source: 'openalex', work_family_id: 'family' },
      { paper_id: 'old-version', title: 'Old version', year: 2020, relevance: 0.6, source: 'arxiv', work_family_id: 'family' },
      { paper_id: 'middle', title: 'Middle', year: 2021, relevance: 0.8, source: 'openalex' },
      { paper_id: 'new', title: 'New', year: 2022, relevance: 0.8, source: 'openalex' },
      { paper_id: 'isolated', title: 'Isolated', year: 2023, relevance: 0.4, source: 'openalex' },
    ],
    [{ source_paper_id: 'middle', target_paper_id: 'old', edge_type: 'cites' }, { source_paper_id: 'new', target_paper_id: 'middle', edge_type: 'cites' }],
  )
  const result = layout(data, { focusNodeId: 'middle' }) // 进入既有一阶邻域模式。
  assert.deepEqual(new Set(result.nodes.map((node) => node.id)), new Set(['family:family', 'paper:middle', 'paper:new'])) // 验证版本族、一阶邻域和孤立节点规则未变化。
  assert.equal(result.mergedVersionNodeCount, 1) // 验证工具栏可区分默认合并的版本节点数量。
})

test('一阶邻域可按新中心重新收敛，只保留该节点的直接关系', () => { // 验证点击邻域内其他节点后，旧中心的非直接关系不会残留在画布中。
  const data = graph( // 构造一条四篇论文的引用链以区分两个相邻中心的可见范围。
    [
      { paper_id: 'a', title: 'A', year: 2020, relevance: 0.8, source: 'openalex' },
      { paper_id: 'b', title: 'B', year: 2021, relevance: 0.8, source: 'openalex' },
      { paper_id: 'c', title: 'C', year: 2022, relevance: 0.8, source: 'openalex' },
      { paper_id: 'd', title: 'D', year: 2023, relevance: 0.8, source: 'openalex' },
    ],
    [
      { source_paper_id: 'a', target_paper_id: 'b', edge_type: 'cites' },
      { source_paper_id: 'b', target_paper_id: 'c', edge_type: 'cites' },
      { source_paper_id: 'c', target_paper_id: 'd', edge_type: 'cites' },
    ],
  )
  const firstCenter = layout(data, { focusNodeId: 'b' }) // 初始中心 B 显示 A、B、C 三个一阶节点。
  const secondCenter = layout(data, { focusNodeId: 'c' }) // 点击 C 后应重新以 C 作为中心显示 B、C、D。
  assert.deepEqual(new Set(firstCenter.nodes.map((node) => node.id)), new Set(['paper:a', 'paper:b', 'paper:c'])) // 确认初始邻域范围正确。
  assert.deepEqual(new Set(secondCenter.nodes.map((node) => node.id)), new Set(['paper:b', 'paper:c', 'paper:d'])) // 确认旧中心的非直接节点 A 已被隐藏。
  assert.ok(secondCenter.edges.every((edge) => edge.sourceId === 'paper:c' || edge.targetId === 'paper:c')) // 新邻域只保留 C 的引用和被引边。
})

test('路径分析可临时恢复研究主干隐藏的真实引用边', () => { // 验证分析显示不会修改完整网络事实集合。
  const backbone = layout(transitiveGraph) // 默认主干隐藏可传递的 A 到 C 直接引用。
  const analysis = layout(transitiveGraph, { forceCitationEdgeIds: ['cites:paper:a:paper:c'] }) // 路径分析请求临时显示该事实边。
  assert.equal(backbone.visibleCitationEdgeCount, 2) // 验证默认主干仍然保持裁剪。
  assert.equal(analysis.visibleCitationEdgeCount, 3) // 验证分析视图恢复真实路径边。
  assert.equal(analysis.temporarilyRevealedCitationEdgeCount, 1) // 验证 UI 可准确提示临时恢复数量。
  assert.equal(analysis.originalCitationEdgeCount, 3) // 验证原始事实边数量始终不变。
})
