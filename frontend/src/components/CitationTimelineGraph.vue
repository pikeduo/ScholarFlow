<script setup lang="ts">
/** 以稳定时间列呈现本次搜索结果内真实引用关系的 D3 组件。 */

import * as d3 from 'd3' // 使用 D3 管理 SVG 元素与受控事件，而非随机力导向布局。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue' // 使用 Vue 生命周期管理可视化资源。
import {
  buildCitationGraphLayout,
  type CitationGraphData,
  type CitationGraphLayout,
  type CitationLayoutEdge,
  type CitationLayoutNode,
  type CitationViewMode,
} from '../utils/citationGraphLayout' // 引入独立的纯数据处理与稳定布局模块。

const props = defineProps<{ graph: CitationGraphData }>() // 接收仅来自已保存搜索结果快照的受限图数据。
const emit = defineEmits<{ (event: 'open-paper', paperId: string): void }>() // 将现有论文详情抽屉的打开行为交回搜索页。

const containerElement = ref<HTMLElement | null>(null) // 保存用于测量可用宽度的容器节点。
const svgElement = ref<SVGSVGElement | null>(null) // 保存 D3 受控的 SVG 根节点。
const measuredWidth = ref(900) // 保存容器宽度，供纯布局函数产生稳定坐标。
const collapseFamilies = ref(true) // 默认把同一版本族合并为一个工作节点。
const includeVersionLinks = ref(false) // 展开版本族后才允许显示黄色虚线事实关系。
const includeIsolates = ref(false) // 默认折叠无引用关系的论文，避免干扰主图。
const viewMode = ref<CitationViewMode>('backbone') // 默认使用研究主干，完整网络由用户主动切换。
const selectedNodeId = ref<string | null>(null) // 保存点击后在侧栏持续展示的论文节点。
const hoveredNodeId = ref<string | null>(null) // 保存悬浮节点，以突出其一阶关系。
const focusedPaperId = ref<string | null>(null) // 保存一阶邻域模式的中心论文标识。
let resizeObserver: ResizeObserver | null = null // 保存容器监听器以便组件卸载时释放。
let simulation: d3.Simulation<CitationLayoutNode, undefined> | null = null // 保留显式清理边界；当前稳定布局不创建力导向 simulation。

const layout = computed<CitationGraphLayout>(() => buildCitationGraphLayout(props.graph, { // 仅从响应事实数据推导可视化状态。
  width: measuredWidth.value, // 将最新容器宽度传入固定时间轴布局。
  collapseFamilies: collapseFamilies.value, // 应用用户选择的版本族合并状态。
  includeVersionLinks: includeVersionLinks.value, // 应用用户选择的版本族虚线状态。
  includeIsolates: includeIsolates.value, // 应用用户选择的孤立论文展开状态。
  focusNodeId: focusedPaperId.value, // 应用一阶邻域过滤状态。
  priorityNodeId: selectedNodeId.value, // 优先保留当前选中论文的直接事实关系。
  viewMode: viewMode.value, // 在纯布局层应用明确的研究主干或完整网络模式。
}))

const nodeById = computed(() => new Map(layout.value.nodes.map((node) => [node.id, node]))) // 建立节点索引以支持侧栏关系列表。
const selectedNode = computed(() => selectedNodeId.value ? nodeById.value.get(selectedNodeId.value) || null : null) // 读取当前被点击的节点。
const selectedCites = computed(() => relatedNodes(selectedNode.value, 'outgoing')) // 读取选中论文直接引用的论文列表。
const selectedCitedBy = computed(() => relatedNodes(selectedNode.value, 'incoming')) // 读取直接引用选中论文的论文列表。
const hasCitationEdges = computed(() => layout.value.originalCitationEdgeCount > 0) // 判断当前节点范围是否存在可核验的原始真实引用边。

function relatedNodes(node: CitationLayoutNode | null, direction: 'incoming' | 'outgoing'): CitationLayoutNode[] { // 根据方向从当前布局读取已可见的相邻论文。
  if (!node) return [] // 未选择论文时不返回关系列表。
  const relatedIds = layout.value.edges // 遍历布局后的可见关系，以遵守一阶邻域和孤立项的当前范围。
    .filter((edge) => edge.edgeType === 'cites') // 只把真实引用边计入引用和被引列表。
    .filter((edge) => direction === 'outgoing' ? edge.sourceId === node.id : edge.targetId === node.id) // 按方向筛选直接关系。
    .map((edge) => direction === 'outgoing' ? edge.targetId : edge.sourceId) // 取得另一端节点标识。
  return [...new Set(relatedIds)].map((id) => nodeById.value.get(id)).filter((item): item is CitationLayoutNode => Boolean(item)) // 去重并过滤不可见节点。
}

function communityColor(community: number, isIsolate: boolean): string { // 为引用分支提供稳定的可区分颜色。
  if (isIsolate) return '#d7e2ea' // 孤立论文使用中性颜色，避免被误解为一个引用社区。
  return d3.schemeTableau10[Math.abs(community) % d3.schemeTableau10.length] || '#4f82a0' // 对社区编号循环取色以保证稳定性。
}

function isNodeRelated(node: CitationLayoutNode, activeNodeId: string | null): boolean { // 判断当前节点是否为悬浮或选中节点的一阶邻居。
  if (!activeNodeId) return true // 未激活关系时所有节点保持正常显示。
  if (node.id === activeNodeId) return true // 始终突出当前中心节点。
  return layout.value.edges.some((edge) => edge.edgeType === 'cites' && ((edge.sourceId === activeNodeId && edge.targetId === node.id) || (edge.targetId === activeNodeId && edge.sourceId === node.id))) // 只根据真实引用关系判断邻接。
}

function isEdgeRelated(edge: CitationLayoutEdge, activeNodeId: string | null): boolean { // 判断边是否应在交互高亮时保留。
  return !activeNodeId || edge.sourceId === activeNodeId || edge.targetId === activeNodeId // 仅突出直接连接到激活论文的边。
}

function renderGraph(): void { // 将当前纯布局状态渲染为 SVG，且不改变其坐标。
  const svgNode = svgElement.value // 读取已挂载的 SVG 根节点。
  if (!svgNode) return // 组件尚未挂载时无需渲染。
  const currentLayout = layout.value // 固化本次渲染使用的布局快照。
  const activeNodeId = hoveredNodeId.value || selectedNodeId.value // 悬浮优先于点击，以提供即时关系反馈。
  const svg = d3.select(svgNode) // 将 SVG 交给 D3 做受控 DOM 更新。
  svg.selectAll('*').remove() // 每次按确定性状态完整重绘，避免残留事件监听器和旧元素。
  svg.attr('viewBox', `0 0 ${currentLayout.width} ${currentLayout.height}`) // 让画布随布局高度扩展并支持响应式缩放。
  svg.attr('aria-label', '本次搜索结果的时间分层引用网络') // 为辅助技术提供图形语义。

  const definitions = svg.append('defs') // 定义真实引用边所需的三种方向箭头。
  const markerDefinitions = [ // 仅定义交互激活时使用的引用方向箭头。
    { id: 'citation-timeline-arrow-active', color: '#2f7598' }, // 悬浮、选中或一阶邻域模式使用深色箭头强化方向。
  ]
  for (const markerDefinition of markerDefinitions) { // 逐个创建固定像素尺寸的 SVG marker。
    definitions.append('marker') // 创建当前状态的箭头 marker。
      .attr('id', markerDefinition.id) // 使用稳定标识供路径按交互状态引用。
      .attr('viewBox', '0 -3 7 6') // 使用约七像素的紧凑箭头坐标系。
      .attr('refX', 7) // 让箭头尖端准确落在已预留间距的路径终点。
      .attr('refY', 0) // 保持箭头围绕路径中心线对齐。
      .attr('markerWidth', 7) // 使用七像素固定宽度，避免细边箭头过大。
      .attr('markerHeight', 7) // 使用七像素固定高度，保持比例一致。
      .attr('markerUnits', 'userSpaceOnUse') // 不随边宽缩放，确保普通和高亮状态方向一致。
      .attr('orient', 'auto') // 让箭头沿各自曲线终点切线自动旋转。
      .append('path') // 绘制紧凑的方向三角形。
      .attr('d', 'M0,-2.6 L7,0 L0,2.6 Z') // 保持尖端清晰并控制在约七像素范围内。
      .attr('fill', markerDefinition.color) // 让箭头颜色与当前边状态匹配。
  }

  const yearX = new Map<number, number>() // 汇总每个年份的固定横轴位置。
  for (const node of currentLayout.nodes) { // 遍历主图节点寻找可用年份列。
    if (!node.isIsolate && node.year !== null && !yearX.has(node.year)) yearX.set(node.year, node.x) // 每个年份只保留一个稳定坐标。
  }
  const guides = svg.append('g').attr('class', 'citation-year-guides') // 绘制时间列和年份文字。
  for (const year of currentLayout.yearTicks) { // 按纯布局返回的稳定年份顺序绘制。
    const x = yearX.get(year) // 读取该年份的横坐标。
    if (x === undefined) continue // 没有可见节点时不绘制空列。
    guides.append('line').attr('x1', x).attr('x2', x).attr('y1', 42).attr('y2', currentLayout.height - 24).attr('stroke', '#dce9f0').attr('stroke-width', 1) // 使用极浅参考线表达时间分层。
    guides.append('text').attr('x', x).attr('y', 28).attr('text-anchor', 'middle').attr('fill', '#547389').attr('font-size', 12).attr('font-weight', 700).text(year) // 标注发表年份而不遮挡节点。
  }

  const edges = svg.append('g').attr('class', 'citation-edges') // 先绘制边，保证节点始终位于上层。
  edges.selectAll<SVGPathElement, CitationLayoutEdge>('path') // 为每条可见事实关系创建路径。
    .data(currentLayout.edges, (edge) => edge.id) // 使用关系主键保持 D3 数据绑定稳定。
    .join('path') // 创建当前状态所需路径。
    .attr('d', (edge) => edge.path) // 使用布局模块计算的避让节点的曲线路径。
    .attr('fill', 'none') // 边不填充任何区域。
    .attr('stroke', (edge) => edge.edgeType === 'same_work' ? '#d8a944' : !activeNodeId ? '#78a8bd' : isEdgeRelated(edge, activeNodeId) ? '#2f7598' : '#c5d9e3') // 默认细浅，悬浮时只加深关联引用边。
    .attr('stroke-width', (edge) => edge.edgeType === 'same_work' ? 1.1 : activeNodeId && isEdgeRelated(edge, activeNodeId) ? 2.3 : 1.15) // 普通边保持细，关联边才明显加粗。
    .attr('stroke-linecap', 'round') // 让曲线路径和箭头连接处更柔和。
    .attr('stroke-linejoin', 'round') // 保持辅助虚线转折处的视觉连续性。
    .attr('stroke-opacity', (edge) => edge.edgeType === 'same_work' ? (activeNodeId && !isEdgeRelated(edge, activeNodeId) ? 0.12 : 0.46) : !activeNodeId ? (viewMode.value === 'backbone' ? 0.22 : 0.12) : isEdgeRelated(edge, activeNodeId) ? 0.96 : 0.05) // 主干默认保持低噪声，完整网络更淡，交互时只强调关联关系。
    .attr('stroke-dasharray', (edge) => edge.edgeType === 'same_work' ? '5 4' : null) // 版本族只在用户显式开启时显示为黄色虚线。
    .attr('marker-end', (edge) => edge.edgeType !== 'cites' ? null : focusedPaperId.value || (activeNodeId && isEdgeRelated(edge, activeNodeId)) ? 'url(#citation-timeline-arrow-active)' : null) // 默认隐藏箭头，仅在交互关系或一阶邻域模式中显示方向。

  const nodes = svg.append('g').attr('class', 'citation-nodes') // 在边之上渲染可交互论文节点。
  const nodeGroups = nodes.selectAll<SVGGElement, CitationLayoutNode>('g') // 绑定布局节点。
    .data(currentLayout.nodes, (node) => node.id) // 以视觉节点标识作为稳定键。
    .join('g') // 创建节点容器。
    .attr('transform', (node) => `translate(${node.x},${node.y})`) // 使用纯布局坐标，不执行随机 simulation。
    .attr('tabindex', 0) // 允许键盘聚焦节点。
    .attr('role', 'button') // 表达点击节点会显示详情。
    .attr('aria-label', (node) => `${node.title}，${node.year || '年份未知'}，入度 ${node.inDegree}，出度 ${node.outDegree}`) // 提供无障碍摘要。
    .style('cursor', 'pointer') // 明确节点可交互。
    .on('mouseenter', (_event, node) => { hoveredNodeId.value = node.id }) // 悬浮时突出一阶引用关系。
    .on('mouseleave', () => { hoveredNodeId.value = null }) // 离开后恢复点击状态或全局状态。
    .on('click', (_event, node) => { selectedNodeId.value = node.id }) // 点击后更新现有侧栏内容。
    .on('keydown', (event, node) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectedNodeId.value = node.id } }) // 允许键盘选择节点。
  nodeGroups.append('circle') // 绘制按入度对数缩放的节点。
    .attr('r', (node) => node.radius) // 直接使用布局函数计算的半径。
    .attr('fill', (node) => communityColor(node.community, node.isIsolate)) // 使用引用社区色或孤立中性色。
    .attr('fill-opacity', (node) => isNodeRelated(node, activeNodeId) ? 0.9 : 0.18) // 非相关节点在交互时明显淡化。
    .attr('stroke', (node) => node.id === selectedNodeId.value ? '#1b4965' : '#3d7895') // 为选中节点提供稳定边框反馈。
    .attr('stroke-width', (node) => node.id === selectedNodeId.value ? 2.7 : 1.5) // 选中节点使用稍粗描边。
  nodeGroups.append('title').text((node) => `${node.title}\n年份：${node.year || '未知'}\n入度：${node.inDegree}，出度：${node.outDegree}${node.memberCount > 1 ? `\n合并版本：${node.memberCount} 篇` : ''}`) // 悬浮时展示完整信息而非永久铺满标题。
  const labelGroups = nodeGroups.filter((node) => node.showLabel || node.id === selectedNodeId.value || node.id === hoveredNodeId.value) // 只为重要或当前交互节点创建位于外侧的标签组。
    .append('g') // 使用独立容器同时承载路径遮挡底板和文字。
    .attr('transform', (node) => `translate(${node.labelBox.x - node.x},${node.labelBox.y - node.y})`) // 直接使用纯布局函数从八个候选位置选择的标签矩形。
    .attr('pointer-events', 'none') // 标签组不抢占节点的悬浮与点击事件。
  labelGroups.append('rect') // 先绘制浅色半透明标签底板，阻断下层引用路径穿过文字。
    .attr('x', 0) // 标签矩形坐标已由布局函数计算为局部原点。
    .attr('y', 0) // 保持标签底板与测量矩形完全一致。
    .attr('width', (node) => node.labelBox.width) // 使用与碰撞检测相同的实际估算宽度。
    .attr('height', (node) => node.labelBox.height) // 使用与碰撞检测相同的实际高度。
    .attr('rx', 4) // 使用小圆角避免形成生硬矩形。
    .attr('fill', '#fbfdfe') // 与图画布浅色底保持一致，遮住边线但不显得像额外卡片。
    .attr('fill-opacity', 0.9) // 保持浅色半透明，同时确保边不会干扰文字阅读。
  labelGroups.append('text') // 在遮挡底板上绘制向外展开的短标签文本。
    .attr('x', 8) // 使用与标签测量一致的左侧内边距。
    .attr('y', 14) // 让十一像素文字基线位于二十像素标签底板内。
    .attr('text-anchor', 'start') // 标签矩形已选定方向，文字在矩形内统一从左开始。
    .attr('fill', '#31576e') // 使用足够深的阅读颜色。
    .attr('font-size', 11) // 降低标签视觉权重。
    .attr('font-weight', 650) // 仍确保小字号可阅读。
    .text((node) => node.labelText) // 渲染参与过实际布局测量的同一份短标题文本。
}

function toggleIsolates(): void { // 在不改变主图布局规则的前提下展开或收起孤立论文网格。
  includeIsolates.value = !includeIsolates.value // 切换孤立论文显示状态。
}

function toggleFamilies(): void { // 切换版本族合并与单独节点模式。
  collapseFamilies.value = !collapseFamilies.value // 反转版本族合并开关。
  if (collapseFamilies.value) includeVersionLinks.value = false // 合并时不再显示已被折叠的同族虚线。
  selectedNodeId.value = null // 避免侧栏保留已不存在的视觉节点。
  focusedPaperId.value = null // 避免用旧标识继续限制新的视图。
}

function setViewMode(nextViewMode: CitationViewMode): void { // 在不修改任何引用事实的前提下切换展示模式。
  viewMode.value = nextViewMode // 由纯布局函数重新计算当前视图的可见边和统计信息。
}

function resetGlobalNetwork(): void { // 返回所有引用分支的全局网络。
  focusedPaperId.value = null // 清除一阶邻域过滤。
}

function focusSelectedNeighborhood(): void { // 仅查看选中论文的一阶邻域。
  const node = selectedNode.value // 读取当前已选择的视觉节点。
  if (!node) return // 未选择论文时不执行过滤。
  focusedPaperId.value = node.paperIds[0] || null // 以代表论文标识传给纯布局函数。
}

function openSelectedPaper(): void { // 从图侧栏复用现有论文详情抽屉。
  const node = selectedNode.value // 读取当前选择的论文或合并版本族。
  const paperId = node?.paperIds[0] // 选择稳定的代表论文打开已有详情。
  if (paperId) emit('open-paper', paperId) // 由父组件执行原有详情读取与展示。
}

function updateMeasuredWidth(): void { // 读取容器宽度并让时间列随可用区域重新计算。
  const width = containerElement.value?.clientWidth || 900 // 容器尚未就绪时提供稳定回退宽度。
  measuredWidth.value = Math.max(680, Math.floor(width)) // 限制最小宽度以保留年份和节点可读性。
}

function disposeRenderer(): void { // 释放本组件创建的可视化资源。
  simulation?.stop() // 即使未来引入局部 simulation，也保证卸载时停止。
  simulation = null // 解除对 simulation 的引用。
  if (svgElement.value) d3.select(svgElement.value).selectAll('*').remove() // 删除 SVG 节点及其 D3 事件监听器。
  resizeObserver?.disconnect() // 停止容器尺寸监听。
  resizeObserver = null // 解除观察器引用。
}

watch([layout, hoveredNodeId, selectedNodeId], async () => { // 布局或交互状态变化时重新渲染受控 SVG。
  await nextTick() // 等待 Vue 将最新容器状态提交到 DOM。
  renderGraph() // 按当前确定性状态重绘。
}, { deep: false })

watch(collapseFamilies, () => { // 展开模式变更后校正版本族虚线开关。
  if (collapseFamilies.value) includeVersionLinks.value = false // 合并模式不得同时显示内部版本族边。
})

onMounted(async () => { // 组件首次挂载后建立响应式测量与初始渲染。
  updateMeasuredWidth() // 首次读取实际容器尺寸。
  resizeObserver = new ResizeObserver(() => updateMeasuredWidth()) // 容器宽度变化时维持稳定时间层比例。
  if (containerElement.value) resizeObserver.observe(containerElement.value) // 仅监听本组件容器，避免全局事件。
  await nextTick() // 等待 SVG 节点完成挂载。
  renderGraph() // 执行初始渲染。
})

onBeforeUnmount(disposeRenderer) // 组件卸载时停止 observer、清空 SVG 并释放可能的 simulation。
</script>

<template>
  <section class="citation-timeline-graph">
    <header class="citation-timeline-toolbar">
      <div>
        <strong>时间分层引用网络</strong>
        <p>A → B 表示论文 A 引用了论文 B；横轴按发表年份从旧到新。</p>
        <div class="citation-timeline-view-switch" role="group" aria-label="引用网络视图">
          <span>视图：</span>
          <button type="button" :class="['citation-timeline-view-button', { 'is-active': viewMode === 'backbone' }]" :aria-pressed="viewMode === 'backbone'" @click="setViewMode('backbone')">研究主干</button>
          <button type="button" :class="['citation-timeline-view-button', { 'is-active': viewMode === 'full' }]" :aria-pressed="viewMode === 'full'" @click="setViewMode('full')">完整网络</button>
        </div>
        <div class="citation-timeline-stats" aria-live="polite">
          <span v-if="viewMode === 'full'">当前显示全部 {{ layout.visibleCitationEdgeCount }} 条内部引用关系</span>
          <span v-else>当前显示：{{ layout.nodes.length }} 篇论文 · {{ layout.visibleCitationEdgeCount }} 条引用</span>
          <span v-if="viewMode === 'backbone' && layout.hiddenCitationEdgeCount">主干模式隐藏 {{ layout.hiddenCitationEdgeCount }} 条次要显示关系</span>
          <span v-if="graph.truncated">节点已由后端裁剪</span>
          <span v-if="!includeIsolates && layout.isolatedCount">另有 {{ layout.isolatedCount }} 篇孤立论文未展开</span>
          <span v-if="collapseFamilies && layout.mergedVersionNodeCount">已合并 {{ layout.mergedVersionNodeCount }} 个版本节点</span>
        </div>
      </div>
      <div class="citation-timeline-actions">
        <button type="button" class="citation-timeline-button" @click="toggleIsolates">
          {{ includeIsolates ? '收起孤立论文' : `展开孤立论文（${layout.isolatedCount}）` }}
        </button>
        <button type="button" class="citation-timeline-button" @click="toggleFamilies">
          {{ collapseFamilies ? '展开版本族' : '合并版本族' }}
        </button>
        <label v-if="!collapseFamilies" class="citation-timeline-check">
          <input v-model="includeVersionLinks" type="checkbox">
          显示版本族虚线
        </label>
        <button v-if="focusedPaperId" type="button" class="citation-timeline-button" @click="resetGlobalNetwork">返回全局网络</button>
      </div>
    </header>

    <div class="citation-timeline-content">
      <div ref="containerElement" class="citation-timeline-canvas">
        <div v-if="!hasCitationEdges" class="citation-timeline-empty">
          当前结果集没有可核验的内部引用关系；论文节点不会补造关键词、作者或模型推断关系。
        </div>
        <svg ref="svgElement" class="citation-timeline-svg" role="img" />
      </div>

      <aside class="citation-timeline-sidebar" aria-live="polite">
        <template v-if="selectedNode">
          <p class="citation-timeline-sidebar-label">已选择论文</p>
          <h4>{{ selectedNode.title }}</h4>
          <p>{{ selectedNode.year || '年份未知' }} · {{ selectedNode.source }}</p>
          <div class="citation-timeline-metrics">
            <span>原始入度 {{ selectedNode.inDegree }}</span>
            <span>原始出度 {{ selectedNode.outDegree }}</span>
            <span v-if="viewMode === 'backbone'">当前入度 {{ selectedNode.displayInDegree }}</span>
            <span v-if="viewMode === 'backbone'">当前出度 {{ selectedNode.displayOutDegree }}</span>
            <span v-if="selectedNode.memberCount > 1">合并版本 {{ selectedNode.memberCount }}</span>
          </div>
          <button type="button" class="citation-timeline-primary" @click="openSelectedPaper">查看论文详情</button>
          <button type="button" class="citation-timeline-button" @click="focusSelectedNeighborhood">仅查看一阶邻域</button>
          <section>
            <h5>该论文引用（{{ selectedCites.length }}）</h5>
            <ul>
              <li v-for="node in selectedCites" :key="node.id">{{ node.title }}</li>
              <li v-if="!selectedCites.length" class="citation-timeline-muted">当前图内没有可核验记录。</li>
            </ul>
          </section>
          <section>
            <h5>引用该论文（{{ selectedCitedBy.length }}）</h5>
            <ul>
              <li v-for="node in selectedCitedBy" :key="node.id">{{ node.title }}</li>
              <li v-if="!selectedCitedBy.length" class="citation-timeline-muted">当前图内没有可核验记录。</li>
            </ul>
          </section>
        </template>
        <template v-else>
          <p class="citation-timeline-sidebar-label">交互提示</p>
          <p>悬浮节点可高亮其直接引用和被引论文；点击节点可查看关系与打开论文详情。</p>
          <p class="citation-timeline-muted">默认只显示真实引用边。黄色虚线仅在展开版本族后手动开启。</p>
        </template>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.citation-timeline-graph { display: grid; gap: 14px; color: #24465b; }
.citation-timeline-toolbar { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.citation-timeline-toolbar strong { font-size: 16px; }
.citation-timeline-toolbar p { margin: 4px 0 0; color: #658096; font-size: 13px; }
.citation-timeline-view-switch { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 10px; color: #587489; font-size: 12px; font-weight: 700; }
.citation-timeline-view-button { border: 1px solid #c3dbe6; border-radius: 7px; padding: 5px 8px; background: #f8fcfe; color: #47718a; font: inherit; font-size: 12px; cursor: pointer; }
.citation-timeline-view-button.is-active { border-color: #347292; background: #e4f2f8; color: #1f5a78; box-shadow: inset 0 0 0 1px rgba(52, 114, 146, .12); }
.citation-timeline-stats { display: flex; flex-wrap: wrap; gap: 5px 8px; margin-top: 8px; color: #617e90; font-size: 11px; line-height: 1.45; }
.citation-timeline-stats span { border-radius: 99px; padding: 3px 7px; background: #f0f7fa; }
.citation-timeline-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.citation-timeline-button, .citation-timeline-primary { border: 1px solid #b8d2df; border-radius: 8px; padding: 7px 10px; background: #f8fcfe; color: #2e637f; font: inherit; font-size: 12px; cursor: pointer; }
.citation-timeline-primary { border-color: #3f7d9c; background: #3f7d9c; color: #fff; }
.citation-timeline-check { display: inline-flex; align-items: center; gap: 5px; color: #6d571e; font-size: 12px; }
.citation-timeline-content { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 14px; min-height: 440px; }
.citation-timeline-canvas { min-width: 0; overflow: auto; border: 1px solid #dceaf0; border-radius: 12px; background: linear-gradient(180deg, #fbfdfe, #f5fafc); }
.citation-timeline-svg { display: block; min-width: 680px; width: 100%; }
.citation-timeline-empty { margin: 16px 16px 0; border-radius: 8px; padding: 10px; background: #f6fafc; color: #607c8f; font-size: 13px; }
.citation-timeline-sidebar { overflow: auto; border-left: 1px solid #deebf1; padding: 4px 0 4px 14px; color: #426275; font-size: 13px; }
.citation-timeline-sidebar h4 { margin: 3px 0 7px; color: #1f4e67; font-size: 15px; line-height: 1.4; }
.citation-timeline-sidebar h5 { margin: 14px 0 6px; color: #386079; font-size: 12px; }
.citation-timeline-sidebar p { margin: 6px 0; line-height: 1.55; }
.citation-timeline-sidebar ul { margin: 0; padding-left: 17px; }
.citation-timeline-sidebar li { margin: 4px 0; line-height: 1.45; }
.citation-timeline-sidebar-label { color: #397491; font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.citation-timeline-metrics { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
.citation-timeline-metrics span { border-radius: 99px; padding: 4px 7px; background: #eaf4f8; color: #2e637f; font-size: 12px; font-weight: 650; }
.citation-timeline-sidebar .citation-timeline-button, .citation-timeline-sidebar .citation-timeline-primary { width: 100%; margin-top: 8px; }
.citation-timeline-muted { color: #758c9a; }
@media (max-width: 900px) { .citation-timeline-toolbar { flex-direction: column; } .citation-timeline-actions { justify-content: flex-start; } .citation-timeline-content { grid-template-columns: 1fr; } .citation-timeline-sidebar { max-height: 260px; border-top: 1px solid #deebf1; border-left: 0; padding: 14px 0 0; } }
</style>
