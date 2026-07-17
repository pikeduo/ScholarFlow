/** 提供仅基于当前搜索快照真实引用边的筛选和局部结构分析。 */

import type { CitationGraphData, VisualEdge } from './citationGraphLayout' // 复用既有图谱事实契约，不创建新的关系类型。

/** 描述结构概览算法需要的最小节点事实字段。 */
export interface CitationAnalysisNode {
  id: string // 保存稳定视觉节点标识。
  year?: number | null // 保存可选发表年份，供时间跨度统计。
}

/** 描述只作用于前端当前图的事实型筛选条件。 */
export interface CitationGraphFilter {
  yearStart?: number | null // 指定可选起始年份。
  yearEnd?: number | null // 指定可选结束年份。
  sources?: string[] // 指定可选来源白名单。
  minimumInDegree?: number // 指定当前结果集内部最小原始入度。
  minimumOutDegree?: number // 指定当前结果集内部最小原始出度。
  includeIsolates?: boolean // 指定是否保留当前结果集内部孤立论文。
}

/** 描述筛选后的兼容图响应和统计信息。 */
export interface CitationGraphFilterResult {
  graph: CitationGraphData // 返回仅保留可见端点与事实边的前端图数据。
  visibleNodeCount: number // 返回筛选后可见论文数量。
  totalNodeCount: number // 返回筛选前当前结果集论文数量。
}

/** 描述只针对当前结果集内部关系的结构指标。 */
export interface CitationGraphMetrics {
  nodeCount: number // 保存当前可见论文数量。
  citationEdgeCount: number // 保存当前可见真实引用边数量。
  componentCount: number // 保存弱连通分量数量。
  isolateCount: number // 保存当前可见孤立论文数量。
  maxInDegreeNodeIds: string[] // 保存内部入度并列最高论文标识。
  maxOutDegreeNodeIds: string[] // 保存内部出度并列最高论文标识。
  earliestYear: number | null // 保存可信年份中的最早年份。
  latestYear: number | null // 保存可信年份中的最新年份。
}

/** 读取当前节点集合内全部有效真实引用边，并过滤版本族和图外端点。 */
function citationEdges(nodes: CitationAnalysisNode[], edges: VisualEdge[]): VisualEdge[] {
  const nodeIds = new Set(nodes.map((node) => node.id)) // 限制统计绝不跨出当前可见节点范围。
  const uniqueEdges = new Map<string, VisualEdge>() // 按稳定边键去重异常重复关系。
  for (const edge of edges) { // 遍历当前布局返回的事实边。
    if (edge.edgeType !== 'cites' || !nodeIds.has(edge.sourceId) || !nodeIds.has(edge.targetId) || edge.sourceId === edge.targetId) continue // 排除版本族、图外端点和自环。
    uniqueEdges.set(`${edge.edgeType}:${edge.sourceId}:${edge.targetId}`, { ...edge }) // 复制关系，避免修改调用方对象。
  }
  return [...uniqueEdges.values()].sort((left, right) => `${left.edgeType}:${left.sourceId}:${left.targetId}`.localeCompare(`${right.edgeType}:${right.sourceId}:${right.targetId}`, 'en')) // 返回跨渲染稳定排序的真实引用边。
}

/** 对 API 图响应应用年份、来源和局部原始度数筛选，且不修改输入。 */
export function filterCitationGraphData(graph: CitationGraphData, filters: CitationGraphFilter): CitationGraphFilterResult {
  const originalNodes = graph.nodes.map((node) => ({ ...node })) // 复制节点防止修改父组件响应。
  const originalEdges = graph.edges.map((edge) => ({ ...edge })) // 复制边防止修改父组件响应。
  const nodeIds = new Set(originalNodes.map((node) => node.paper_id)) // 限定当前搜索结果快照中的节点范围。
  const inDegree = new Map<string, number>(originalNodes.map((node) => [node.paper_id, 0])) // 初始化全部节点的局部入度。
  const outDegree = new Map<string, number>(originalNodes.map((node) => [node.paper_id, 0])) // 初始化全部节点的局部出度。
  for (const edge of originalEdges) { // 只以保存的真实引用计算局部度数。
    if (edge.edge_type !== 'cites' || !nodeIds.has(edge.source_paper_id) || !nodeIds.has(edge.target_paper_id) || edge.source_paper_id === edge.target_paper_id) continue // 跳过图外、版本族和自环关系。
    inDegree.set(edge.target_paper_id, (inDegree.get(edge.target_paper_id) || 0) + 1) // 被引用论文增加入度。
    outDegree.set(edge.source_paper_id, (outDegree.get(edge.source_paper_id) || 0) + 1) // 引用其他论文的节点增加出度。
  }
  const sources = filters.sources ? new Set(filters.sources) : null // 规范化可选来源白名单。
  const visibleNodes = originalNodes.filter((node) => { // 对每篇当前结果集论文应用事实型条件。
    if (filters.yearStart !== null && filters.yearStart !== undefined && (node.year === null || node.year < filters.yearStart)) return false // 缺失或早于起始年份时隐藏。
    if (filters.yearEnd !== null && filters.yearEnd !== undefined && (node.year === null || node.year > filters.yearEnd)) return false // 缺失或晚于结束年份时隐藏。
    if (sources && !sources.has(node.source)) return false // 来源不在用户选择范围时隐藏。
    if ((inDegree.get(node.paper_id) || 0) < Math.max(0, filters.minimumInDegree || 0)) return false // 使用当前结果集内部原始入度筛选。
    if ((outDegree.get(node.paper_id) || 0) < Math.max(0, filters.minimumOutDegree || 0)) return false // 使用当前结果集内部原始出度筛选。
    return filters.includeIsolates !== false || (inDegree.get(node.paper_id) || 0) > 0 || (outDegree.get(node.paper_id) || 0) > 0 // 仅在用户要求时隐藏内部孤立论文。
  })
  const visibleIds = new Set(visibleNodes.map((node) => node.paper_id)) // 收集筛选后仍可见的端点。
  const visibleEdges = originalEdges.filter((edge) => visibleIds.has(edge.source_paper_id) && visibleIds.has(edge.target_paper_id)) // 删除任一端点被隐藏的关系。
  return { graph: { ...graph, nodes: visibleNodes, edges: visibleEdges }, visibleNodeCount: visibleNodes.length, totalNodeCount: originalNodes.length } // 保持后端响应其余字段不变。
}

/** 计算当前可见图的内部结构指标，绝不解释为全局学术影响力。 */
export function analyzeCitationGraph(nodes: CitationAnalysisNode[], edges: VisualEdge[]): CitationGraphMetrics {
  const citations = citationEdges(nodes, edges) // 仅保留当前范围内的真实引用边。
  const inDegree = new Map<string, number>(nodes.map((node) => [node.id, 0])) // 初始化当前可见节点入度。
  const outDegree = new Map<string, number>(nodes.map((node) => [node.id, 0])) // 初始化当前可见节点出度。
  const adjacent = new Map<string, Set<string>>(nodes.map((node) => [node.id, new Set<string>()])) // 为弱连通分量建立无向邻接。
  for (const edge of citations) { // 累加度数并记录不受方向影响的结构连接。
    inDegree.set(edge.targetId, (inDegree.get(edge.targetId) || 0) + 1) // 统计当前结果集内部被引次数。
    outDegree.set(edge.sourceId, (outDegree.get(edge.sourceId) || 0) + 1) // 统计当前结果集内部引用次数。
    adjacent.get(edge.sourceId)?.add(edge.targetId) // 建立无向分量连接。
    adjacent.get(edge.targetId)?.add(edge.sourceId) // 建立反向无向分量连接。
  }
  const visited = new Set<string>() // 记录已归类节点，避免重复计数分量。
  let componentCount = 0 // 累积弱连通分量数量。
  for (const node of [...nodes].sort((left, right) => left.id.localeCompare(right.id, 'en'))) { // 按稳定顺序遍历全部节点。
    if (visited.has(node.id)) continue // 已归入前序分量时跳过。
    componentCount += 1 // 新起点形成一个新的弱连通分量。
    const queue = [node.id] // 使用队列执行有限 BFS。
    visited.add(node.id) // 起点立即标记为已访问。
    for (let cursor = 0; cursor < queue.length; cursor += 1) { // 稳定遍历当前分量。
      for (const nextId of adjacent.get(queue[cursor]) || []) { // 读取当前节点的无向邻居。
        if (visited.has(nextId)) continue // 已访问邻居无需再次入队。
        visited.add(nextId) // 记录首次访问。
        queue.push(nextId) // 继续扩展同一弱连通分量。
      }
    }
  }
  const maxInDegree = Math.max(0, ...inDegree.values()) // 读取当前结果集内部最大入度。
  const maxOutDegree = Math.max(0, ...outDegree.values()) // 读取当前结果集内部最大出度。
  const years = nodes.map((node) => node.year).filter((year): year is number => typeof year === 'number' && Number.isFinite(year)) // 仅收集可信年份。
  return { nodeCount: nodes.length, citationEdgeCount: citations.length, componentCount, isolateCount: nodes.filter((node) => (inDegree.get(node.id) || 0) === 0 && (outDegree.get(node.id) || 0) === 0).length, maxInDegreeNodeIds: maxInDegree ? nodes.filter((node) => (inDegree.get(node.id) || 0) === maxInDegree).map((node) => node.id).sort((left, right) => left.localeCompare(right, 'en')) : [], maxOutDegreeNodeIds: maxOutDegree ? nodes.filter((node) => (outDegree.get(node.id) || 0) === maxOutDegree).map((node) => node.id).sort((left, right) => left.localeCompare(right, 'en')) : [], earliestYear: years.length ? Math.min(...years) : null, latestYear: years.length ? Math.max(...years) : null } // 返回局部事实指标并明确空图安全值。
}
