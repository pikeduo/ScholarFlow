/** 验证节点选择、固定邻域焦点和 SVG 边绑定范围彼此独立。 */

import assert from 'node:assert/strict' // 使用 Node 内置断言避免测试依赖浏览器或后端。
import test from 'node:test' // 使用轻量 Node 测试运行器执行纯交互规则。
import { buildCitationGraphLayout, type CitationGraphData } from '../src/utils/citationGraphLayout.ts' // 使用真实纯布局函数验证节点范围。
import { clearCitationGraphSelection, filterRelationshipEdges, focusCitationGraphPaper, resetCitationGraphFocus, resolveRelationshipNodeId, selectCitationGraphNode, type CitationGraphInteractionState } from '../src/utils/citationGraphInteraction.ts' // 导入受测的状态转换和边过滤规则。

const initialState: CitationGraphInteractionState = { selectedNodeId: null, focusedPaperId: null, hoveredNodeId: null } // 构造首次打开引用图时的三个独立状态。
const graph: CitationGraphData = { // 构造需求中的 A、B、C、D、E 五节点引用事实。
  nodes: [
    { paper_id: 'a', title: 'A', year: 2020, relevance: 0.8, source: 'openalex' },
    { paper_id: 'b', title: 'B', year: 2021, relevance: 0.8, source: 'openalex' },
    { paper_id: 'c', title: 'C', year: 2022, relevance: 0.8, source: 'openalex' },
    { paper_id: 'd', title: 'D', year: 2023, relevance: 0.8, source: 'openalex' },
    { paper_id: 'e', title: 'E', year: 2024, relevance: 0.8, source: 'openalex' },
  ],
  edges: [
    { source_paper_id: 'a', target_paper_id: 'b', edge_type: 'cites' },
    { source_paper_id: 'c', target_paper_id: 'a', edge_type: 'cites' },
    { source_paper_id: 'b', target_paper_id: 'd', edge_type: 'cites' },
    { source_paper_id: 'e', target_paper_id: 'c', edge_type: 'cites' },
  ],
  truncated: false,
  max_nodes: 30,
}

function layout(focusNodeId: string | null = null) { // 构造与组件相同的一阶邻域布局请求。
  return buildCitationGraphLayout(graph, { width: 960, collapseFamilies: true, includeVersionLinks: false, includeIsolates: true, focusNodeId, viewMode: 'full' }) // 完整网络避免主干裁剪掩盖关系选择语义。
}

test('初始状态保留全局节点和全部边，关系中心为空', () => { // 验证首次打开不自动进入邻域或选中任何论文。
  const globalLayout = layout() // 计算没有固定邻域焦点的全局布局。
  assert.equal(resolveRelationshipNodeId(initialState.hoveredNodeId, initialState.selectedNodeId), null) // 初始状态不产生关系高亮中心。
  assert.equal(globalLayout.nodes.length, 5) // 全局节点集合完整保留五篇论文。
  assert.equal(filterRelationshipEdges(globalLayout.edges, null).length, 4) // 无关系中心时全部边将进入 SVG 绑定以显示淡化轮廓和箭头。
})

test('普通选择只改变 selectedNodeId，并让无关边不进入渲染集合', () => { // 验证全局点击 A 不会裁剪节点集合或更新固定邻域焦点。
  const selected = selectCitationGraphNode(initialState, 'paper:a') // 模拟鼠标点击或键盘 Enter、Space 选择 A。
  const globalLayout = layout() // 选择不传入布局焦点，因此全局布局应保持不变。
  const renderedEdges = filterRelationshipEdges(globalLayout.edges, resolveRelationshipNodeId(selected.hoveredNodeId, selected.selectedNodeId)) // 仅派生与 A 相连的 SVG 数据。
  assert.equal(selected.focusedPaperId, null) // 普通点击绝不进入一阶邻域。
  assert.equal(globalLayout.nodes.length, 5) // 普通点击不删除任何全局节点。
  assert.deepEqual(renderedEdges.map((edge) => edge.id), ['cites:paper:a:paper:b', 'cites:paper:c:paper:a']) // 只有 A 的入边和出边会被绑定，B 到 D、E 到 C 不会留下路径或 marker。
})

test('一阶邻域由显式焦点固定，邻域内选择 B 不会重新裁剪节点集合', () => { // 验证 A 邻域中普通点击 B 只切换关系中心。
  const selectedA = selectCitationGraphNode(initialState, 'paper:a') // 先选择 A 以模拟用户打开侧栏。
  const focusedA = focusCitationGraphPaper(selectedA, 'a') // 仅模拟用户点击“一阶邻域”按钮时才写入 A 焦点。
  const aNeighborhood = layout(focusedA.focusedPaperId) // A 的一阶节点应仅为 A、B、C。
  const selectedB = selectCitationGraphNode(focusedA, 'paper:b') // 在 A 邻域中普通点击 B。
  const unchangedNeighborhood = layout(selectedB.focusedPaperId) // 因焦点仍为 A，节点集合必须不变。
  const renderedEdges = filterRelationshipEdges(unchangedNeighborhood.edges, resolveRelationshipNodeId(selectedB.hoveredNodeId, selectedB.selectedNodeId)) // 仅显示 B 在 A 邻域内的直接关系。
  assert.equal(selectedB.focusedPaperId, 'a') // 普通点击 B 不得把邻域中心改为 B。
  assert.deepEqual(new Set(aNeighborhood.nodes.map((node) => node.id)), new Set(['paper:a', 'paper:b', 'paper:c'])) // 首次邻域只保留 A 和直接邻居。
  assert.deepEqual(new Set(unchangedNeighborhood.nodes.map((node) => node.id)), new Set(['paper:a', 'paper:b', 'paper:c'])) // 点击 B 后节点集合仍为 A 的邻域。
  assert.deepEqual(renderedEdges.map((edge) => edge.id), ['cites:paper:a:paper:b']) // B 到 D 因 D 不在当前布局中而不会进入 SVG。
})

test('仅在再次明确聚焦 B 时才重新计算 B 的一阶邻域', () => { // 验证从 A 邻域迁移到 B 邻域需要单独按钮操作。
  const focusedA = focusCitationGraphPaper(selectCitationGraphNode(initialState, 'paper:a'), 'a') // 构造已进入 A 邻域的状态。
  const selectedB = selectCitationGraphNode(focusedA, 'paper:b') // 普通点击 B 后仍保留 A 焦点。
  const focusedB = focusCitationGraphPaper(selectedB, 'b') // 再次点击按钮后才将焦点切换到 B。
  const bNeighborhood = layout(focusedB.focusedPaperId) // 重新计算 B 的一阶邻域。
  assert.equal(focusedB.selectedNodeId, 'paper:b') // 切换焦点不丢失当前侧栏选择。
  assert.equal(focusedB.focusedPaperId, 'b') // 只有显式聚焦操作才更新邻域中心。
  assert.deepEqual(new Set(bNeighborhood.nodes.map((node) => node.id)), new Set(['paper:a', 'paper:b', 'paper:d'])) // B 邻域此时才显示 A、B、D。
})

test('悬浮只临时覆盖关系展示，返回全局和清除选择保持职责独立', () => { // 验证 hover、返回全局、清除选择不会互相篡改不该负责的状态。
  const focused = focusCitationGraphPaper(selectCitationGraphNode(initialState, 'paper:a'), 'a') // 构造已选择并进入 A 邻域的状态。
  const hoveredState = { ...focused, hoveredNodeId: 'paper:b' } // 模拟鼠标移入 B，且不调用任何选择或焦点转换。
  const global = resetCitationGraphFocus(hoveredState) // 用户点击返回全局网络。
  const cleared = clearCitationGraphSelection(focused) // 用户单独清除选择。
  assert.equal(resolveRelationshipNodeId(hoveredState.hoveredNodeId, hoveredState.selectedNodeId), 'paper:b') // hover 临时覆盖边展示中心。
  assert.equal(hoveredState.selectedNodeId, 'paper:a') // hover 不改变持久选择。
  assert.equal(hoveredState.focusedPaperId, 'a') // hover 不改变一阶邻域中心。
  assert.equal(global.selectedNodeId, 'paper:a') // 返回全局只清空焦点而保留当前选择。
  assert.equal(global.focusedPaperId, null) // 返回全局使下次布局恢复完整节点集合。
  assert.equal(cleared.selectedNodeId, null) // 清除选择移除持久关系中心。
  assert.equal(cleared.hoveredNodeId, null) // 清除选择同时移除临时 hover 状态。
  assert.equal(cleared.focusedPaperId, 'a') // 清除选择不得隐式退出一阶邻域。
})
