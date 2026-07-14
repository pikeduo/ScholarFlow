/** 验证自适应时间分层引用图的纯函数布局、标签、端口和通道路由规则。 */

import assert from 'node:assert/strict' // 使用 Node 内置断言验证确定性布局输出。
import test from 'node:test' // 使用 Node 内置测试运行器保持测试轻量。
import { assignYearColumns, buildCitationGraphLayout, measureLabelBoxes, type CitationGraphData } from '../src/utils/citationGraphLayout.ts' // 引入被测纯函数和数据契约。

function graph(nodes: CitationGraphData['nodes'], edges: CitationGraphData['edges']): CitationGraphData { // 快速构造固定的受限引用图响应。
  return { nodes, edges, truncated: false, max_nodes: 30 } // 保持与生产 API 响应一致的最小字段。
}

function layout(data: CitationGraphData, width = 960) { // 为测试提供统一布局选项。
  return buildCitationGraphLayout(data, { width, collapseFamilies: true, includeVersionLinks: false, includeIsolates: false }) // 使用默认版本族合并和孤立论文折叠策略。
}

const chain = graph( // 构造包含缺失年份和跨多年关系的基准图。
  [
    { paper_id: 'old', title: 'Foundational retrieval paper', year: 2018, relevance: 0.8, source: 'openalex' },
    { paper_id: 'middle', title: 'Intermediate retrieval paper', year: 2021, relevance: 0.7, source: 'openalex' },
    { paper_id: 'recent', title: 'Recent retrieval application', year: 2024, relevance: 0.9, source: 'semantic_scholar' },
  ],
  [
    { source_paper_id: 'middle', target_paper_id: 'old', edge_type: 'cites' },
    { source_paper_id: 'recent', target_paper_id: 'middle', edge_type: 'cites' },
    { source_paper_id: 'recent', target_paper_id: 'old', edge_type: 'cites' },
  ],
)

test('实际出现年份采用等间距序数列，缺失年份不产生空白', () => { // 验证年份差值不会影响横向间距。
  const columns = assignYearColumns([2018, 2021, 2029], 900) // 传入年份间隔不均匀的三列。
  const first = columns.get(2018) || 0 // 读取第一列坐标。
  const middle = columns.get(2021) || 0 // 读取中间列坐标。
  const last = columns.get(2029) || 0 // 读取最后一列坐标。

  assert.equal(middle - first, last - middle) // 验证每个实际年份列等间距。
})

test('两个年份边连接圆周端口，箭头终点留在目标圆外', () => { // 验证最小跨年份图不依赖标签侧特例。
  const result = layout(graph( // 构造两个年份的一条引用边。
    [{ paper_id: 'a', title: 'A', year: 2020, relevance: 0.8, source: 'openalex' }, { paper_id: 'b', title: 'B', year: 2023, relevance: 0.8, source: 'openalex' }],
    [{ source_paper_id: 'b', target_paper_id: 'a', edge_type: 'cites' }],
  ))
  const edge = result.edges[0] // 读取唯一边。
  const source = result.nodes.find((node) => node.id === 'paper:b') // 读取源节点。
  const target = result.nodes.find((node) => node.id === 'paper:a') // 读取目标节点。
  const end = edge.points.at(-1) // 读取最终路径点以验证箭头切线端点。

  assert.ok(source && target && end) // 确认端点节点和路径都存在。
  assert.notDeepEqual(edge.points[0], { x: source.x, y: source.y }) // 验证边不从节点中心出发。
  assert.ok(Math.hypot(end.x - target.x, end.y - target.y) > target.radius) // 验证箭头尖端在目标圆周外留有安全距离。
})

test('三个以上年份的长引用边按每个年份间通道分段', () => { // 验证长边不会直接横跨整个画布。
  const result = layout(chain) // 计算包含三个实际年份列的布局。
  const longEdge = result.edges.find((edge) => edge.id === 'cites:paper:recent:paper:old') // 定位跨越多个年份的直接引用。

  assert.ok(longEdge) // 确认长边存在。
  assert.ok(longEdge.points.length >= 5) // 验证路径至少保留源端、多个通道折点和目标端。
  assert.match(longEdge.path, /Q/) // 验证正交通道路由在折点使用圆角而非固定贝塞尔模板。
})

test('同一年多个节点的引用边使用动态外部通道', () => { // 验证同年边不会固定只走一侧。
  const result = layout(graph( // 构造三篇同年论文和两条共享目标的引用。
    [
      { paper_id: 'a', title: 'A', year: 2023, relevance: 0.8, source: 'openalex' },
      { paper_id: 'b', title: 'B', year: 2023, relevance: 0.8, source: 'openalex' },
      { paper_id: 'c', title: 'C', year: 2023, relevance: 0.8, source: 'openalex' },
    ],
    [{ source_paper_id: 'a', target_paper_id: 'b', edge_type: 'cites' }, { source_paper_id: 'c', target_paper_id: 'b', edge_type: 'cites' }],
  ))
  const target = result.nodes.find((node) => node.id === 'paper:b') // 读取同年边共享的目标列。
  const lanes = result.edges.map((edge) => edge.points[1]?.x) // 读取同年边外部通道横坐标。

  assert.ok(target) // 确认目标节点存在。
  assert.equal(new Set(lanes).size, 2) // 验证多条边分配独立轨道。
  assert.ok(lanes.some((lane) => typeof lane === 'number' && lane < target.x) || lanes.some((lane) => typeof lane === 'number' && lane > target.x)) // 验证车道由算法选择为列外通道而非时间线中心。
})

test('双向边和同一目标多入边分散端口', () => { // 验证端口占用会参与动态选择。
  const result = layout(graph( // 构造双向边和三条指向同一节点的边。
    [
      { paper_id: 'a', title: 'A', year: 2020, relevance: 0.8, source: 'openalex' },
      { paper_id: 'b', title: 'B', year: 2021, relevance: 0.8, source: 'openalex' },
      { paper_id: 'c', title: 'C', year: 2022, relevance: 0.8, source: 'openalex' },
      { paper_id: 'd', title: 'D', year: 2023, relevance: 0.8, source: 'openalex' },
    ],
    [
      { source_paper_id: 'a', target_paper_id: 'b', edge_type: 'cites' }, { source_paper_id: 'b', target_paper_id: 'a', edge_type: 'cites' },
      { source_paper_id: 'c', target_paper_id: 'b', edge_type: 'cites' }, { source_paper_id: 'd', target_paper_id: 'b', edge_type: 'cites' },
    ],
  ))
  const incomingPorts = result.edges.filter((edge) => edge.targetId === 'paper:b').map((edge) => edge.targetPort) // 收集多入边在目标节点使用的端口。

  assert.ok(new Set(incomingPorts).size >= 2) // 验证端口占用避免所有入边堆叠到同一端口。
})

test('长标题会先测量后参与标签候选布局', () => { // 验证文字不是最终渲染时才参与避让。
  const result = layout(graph( // 构造带长标题的两篇相关论文。
    [{ paper_id: 'a', title: 'A very long paper title that should be measured before it is placed in the citation network', year: 2020, relevance: 0.8, source: 'openalex' }, { paper_id: 'b', title: 'Short title', year: 2021, relevance: 0.8, source: 'openalex' }],
    [{ source_paper_id: 'b', target_paper_id: 'a', edge_type: 'cites' }],
  ))
  const longNode = result.nodes.find((node) => node.id === 'paper:a') // 读取长标题节点。
  const measured = measureLabelBoxes(result.nodes).get('paper:a') // 独立复算同一标题测量值。

  assert.ok(longNode && measured) // 确认节点和测量结果存在。
  assert.equal(longNode.labelBox.width, measured.width) // 验证最终布局直接使用测量宽度。
  assert.ok(longNode.labelText.endsWith('…')) // 验证默认标签采用语义缩放后的短标题。
})

test('窄画布减少默认标签数量但保留悬浮和选中所需坐标', () => { // 验证空间不足时不强制显示所有标题。
  const result = layout(chain, 680) // 使用接近组件最小宽度的窄画布。

  assert.ok(result.nodes.filter((node) => node.showLabel).length <= 5) // 验证默认常驻标签预算缩小。
  assert.ok(result.nodes.every((node) => node.labelBox.width > 0 && node.labelBox.height > 0)) // 验证隐藏节点仍有可用于悬浮显示的计算位置。
})

test('一阶邻域、版本族合并和孤立节点策略保持不变', () => { // 验证重构未改变既有筛选与交互契约。
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
  const result = buildCitationGraphLayout(data, { width: 960, collapseFamilies: true, includeVersionLinks: false, includeIsolates: false, focusNodeId: 'middle' }) // 进入既有一阶邻域模式。

  assert.deepEqual(new Set(result.nodes.map((node) => node.id)), new Set(['family:family', 'paper:middle', 'paper:new'])) // 验证版本族、一阶邻域和孤立节点规则未变化。
})
