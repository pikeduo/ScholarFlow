/** 提供仅基于当前搜索快照真实引用边的路径、邻域、筛选和局部结构分析。 */

import type { CitationGraphData, VisualEdge } from './citationGraphLayout' // 复用既有图谱事实契约，不创建新的关系类型。

export interface CitationAnalysisNode { // 描述路径与指标算法需要的最小节点事实字段。
  id: string // 保存稳定节点标识。
  title?: string // 保存可选标题，供结构指标返回可读节点。
  year?: number | null // 保存可选年份，供时间跨度统计。
  source?: string // 保存可选来源，供事实型筛选。
}

export interface CitationPathOptions { // 描述路径查询的受控遍历边界。
  directed: boolean // 指定是否严格沿 A 引用 B 的事实方向前进。
  maxDepth: number // 指定单条路径允许的最大引用边数。
  maxPaths: number // 指定最多返回多少条同长度最短路径。
}

export interface CitationPath { // 描述一条可审计的引用连接路径。
  nodeIds: string[] // 按实际遍历顺序保存经过论文节点。
  edgeIds: string[] // 保存原始事实引用边的稳定标识。
  length: number // 保存路径包含的真实引用边数量。
}

export interface CitationPathResult { // 描述受控路径查询的完整返回值。
  paths: CitationPath[] // 返回按长度和稳定标识排序的最短路径集合。
  truncated: boolean // 标记是否因最大路径数截断同长度候选。
  visitedNodeCount: number // 返回本次搜索实际访问过的节点数。
}

export interface CitationNeighborhood { // 描述按方向展开的多层真实引用邻域。
  levels: Array<{ depth: number, nodeIds: string[] }> // 按首次到达的最短层级组织节点。
  edgeIds: string[] // 保存所有实际经过的真实引用边。
}

export interface CitationGraphFilter { // 描述只作用于前端当前图的事实型筛选条件。
  yearStart?: number | null // 指定可选起始年份。
  yearEnd?: number | null // 指定可选结束年份。
  sources?: string[] // 指定可选来源白名单。
  minimumInDegree?: number // 指定当前结果集内部最小原始入度。
  minimumOutDegree?: number // 指定当前结果集内部最小原始出度。
  includeIsolates?: boolean // 指定是否保留当前结果集内部孤立论文。
}

export interface CitationGraphFilterResult { // 描述筛选后的兼容图响应和统计信息。
  graph: CitationGraphData // 返回仅保留可见端点与事实边的前端图数据。
  visibleNodeCount: number // 返回筛选后可见论文数量。
  totalNodeCount: number // 返回筛选前当前结果集论文数量。
}

export interface CitationGraphMetrics { // 描述只针对当前结果集内部关系的结构指标。
  nodeCount: number // 保存当前可见论文数量。
  citationEdgeCount: number // 保存当前可见真实引用边数量。
  componentCount: number // 保存弱连通分量数量。
  isolateCount: number // 保存当前可见孤立论文数量。
  maxInDegreeNodeIds: string[] // 保存内部入度并列最高论文标识。
  maxOutDegreeNodeIds: string[] // 保存内部出度并列最高论文标识。
  earliestYear: number | null // 保存可信年份中的最早年份。
  latestYear: number | null // 保存可信年份中的最晚年份。
}

/** 生成与布局模块一致的稳定真实引用边标识。 */
export function citationEdgeId(edge: Pick<VisualEdge, 'sourceId' | 'targetId' | 'edgeType'>): string { // 接收最小关系字段。
  return `${edge.edgeType}:${edge.sourceId}:${edge.targetId}` // 保持事实边键可跨布局和分析模块复用。
}

/** 只保留端点存在的真实引用边并建立统一的确定性双向邻接表。 */
export function buildCitationAdjacency(nodes: CitationAnalysisNode[], edges: VisualEdge[]) { // 返回路径、邻域和指标共享的邻接索引。
  const nodeIds = new Set(nodes.map((node) => node.id)) // 限制分析绝不跨出当前可见节点范围。
  const citations = new Map<string, VisualEdge>() // 以稳定边键去重真实引用关系。
  for (const edge of edges) { // 遍历当前可见图的事实关系。
    if (edge.edgeType !== 'cites' || !nodeIds.has(edge.sourceId) || !nodeIds.has(edge.targetId) || edge.sourceId === edge.targetId) continue // 排除版本族、图外端点和自环。
    citations.set(citationEdgeId(edge), { ...edge }) // 复制边，确保不修改调用方对象。
  }
  const orderedEdges = [...citations.values()].sort((left, right) => citationEdgeId(left).localeCompare(citationEdgeId(right), 'en')) // 固定所有后续遍历顺序。
  const outgoing = new Map<string, VisualEdge[]>() // 保存沿 A → B 前进的前置引用邻接。
  const incoming = new Map<string, VisualEdge[]>() // 保存沿入边反向前进的后续被引邻接。
  for (const nodeId of nodeIds) { outgoing.set(nodeId, []); incoming.set(nodeId, []) } // 先为全部节点初始化空集合。
  for (const edge of orderedEdges) { // 逐条写入两个方向的索引。
    outgoing.get(edge.sourceId)?.push(edge) // A 的出边指向 A 引用的前置工作。
    incoming.get(edge.targetId)?.push(edge) // B 的入边来自引用 B 的后续论文。
  }
  return { nodeIds, edges: orderedEdges, outgoing, incoming } // 返回唯一的邻接表实现供所有算法复用。
}

/** 在受控深度内按 BFS 查找多条最短引用路径，并安全处理循环。 */
export function findCitationPaths(nodes: CitationAnalysisNode[], edges: VisualEdge[], startNodeId: string, endNodeId: string, options: CitationPathOptions): CitationPathResult { // 返回不生成新边的事实路径结果。
  const adjacency = buildCitationAdjacency(nodes, edges) // 使用统一事实邻接表。
  const maxDepth = Math.min(6, Math.max(0, Math.floor(options.maxDepth))) // 强制路径深度处于产品安全范围。
  const maxPaths = Math.min(10, Math.max(1, Math.floor(options.maxPaths))) // 强制返回路径数量处于产品安全范围。
  if (!adjacency.nodeIds.has(startNodeId) || !adjacency.nodeIds.has(endNodeId)) return { paths: [], truncated: false, visitedNodeCount: 0 } // 不存在端点时返回明确空结果。
  if (startNodeId === endNodeId) return { paths: [{ nodeIds: [startNodeId], edgeIds: [], length: 0 }], truncated: false, visitedNodeCount: 1 } // 相同端点返回受控零长度路径。
  const queue: CitationPath[] = [{ nodeIds: [startNodeId], edgeIds: [], length: 0 }] // 初始化 BFS 队列。
  const visitedNodes = new Set<string>([startNodeId]) // 统计搜索过程触及的节点，而非剪枝全局访问状态。
  const paths: CitationPath[] = [] // 累积同长度最短路径。
  let shortestLength: number | null = null // 记录首次抵达终点时的最短长度。
  let truncated = false // 记录是否存在因数量上限未返回的同长度路径。
  for (let cursor = 0; cursor < queue.length; cursor += 1) { // 使用游标稳定遍历队列。
    const current = queue[cursor] // 读取当前候选路径。
    if (shortestLength !== null && current.length >= shortestLength) continue // 已取得最短路径后不再扩展更长路径。
    if (current.length >= maxDepth) continue // 到达用户深度上限时停止扩展。
    const currentId = current.nodeIds[current.nodeIds.length - 1] || startNodeId // 使用兼容当前 TypeScript 目标库的索引方式读取路径末端节点。
    const directedEdges = adjacency.outgoing.get(currentId) || [] // 沿真实 A → B 方向查找前置引用。
    const undirectedEdges = options.directed ? directedEdges : [...directedEdges, ...(adjacency.incoming.get(currentId) || [])].sort((left, right) => citationEdgeId(left).localeCompare(citationEdgeId(right), 'en')) // 忽略方向时仅扩大结构探索，不改变边事实方向。
    for (const edge of undirectedEdges) { // 逐条尝试扩展当前简单路径。
      const nextId = edge.sourceId === currentId ? edge.targetId : edge.sourceId // 在无向探索时选择关系另一端节点。
      if (current.nodeIds.includes(nextId)) continue // 单条路径中禁止重复节点，从而安全处理环。
      const nextPath = { nodeIds: [...current.nodeIds, nextId], edgeIds: [...current.edgeIds, citationEdgeId(edge)], length: current.length + 1 } // 构造不修改原路径的新候选。
      visitedNodes.add(nextId) // 记录已实际触及的节点。
      if (nextId === endNodeId) { // 找到终点时只收集最短长度候选。
        if (shortestLength === null) shortestLength = nextPath.length // 首次到达必为 BFS 最短距离。
        if (nextPath.length !== shortestLength) continue // 更长路径不属于默认返回集合。
        if (paths.length < maxPaths) paths.push(nextPath) // 在数量上限内保留稳定路径。
        else truncated = true // 标记仍存在未返回的同长度候选。
        continue // 终点路径无需继续扩展。
      }
      queue.push(nextPath) // 非终点节点继续加入下一层 BFS。
    }
  }
  paths.sort((left, right) => left.length - right.length || left.nodeIds.join('\u0000').localeCompare(right.nodeIds.join('\u0000'), 'en')) // 固定多条路径的返回顺序。
  return { paths, truncated, visitedNodeCount: visitedNodes.size } // 返回受控、可审计的路径结果。
}

/** 沿指定真实引用方向逐层收集邻域，节点只在首次最短层级出现。 */
function collectCitationNeighborhood(nodeId: string, nodes: CitationAnalysisNode[], edges: VisualEdge[], maxDepth: number, direction: 'outgoing' | 'incoming'): CitationNeighborhood { // 为前置工作和后续引用提供共享实现。
  const adjacency = buildCitationAdjacency(nodes, edges) // 使用统一事实邻接表。
  const depthLimit = Math.min(3, Math.max(1, Math.floor(maxDepth))) // 多层探索严格限制为一到三层。
  if (!adjacency.nodeIds.has(nodeId)) return { levels: [], edgeIds: [] } // 图中不存在中心论文时返回空邻域。
  const visited = new Set<string>([nodeId]) // 记录首次到达节点，避免循环和重复层级。
  const queue: Array<{ nodeId: string, depth: number }> = [{ nodeId, depth: 0 }] // 从中心论文开始广度扩展。
  const levels = new Map<number, string[]>() // 按最短距离组织发现节点。
  const edgeIds = new Set<string>() // 收集实际遍历的真实引用边。
  for (let cursor = 0; cursor < queue.length; cursor += 1) { // 稳定遍历 BFS 队列。
    const current = queue[cursor] // 读取当前节点及其层级。
    if (current.depth >= depthLimit) continue // 已到达用户上限时停止继续扩展。
    for (const edge of adjacency[direction].get(current.nodeId) || []) { // 前置工作沿出边，后续引用沿入边。
      const nextId = direction === 'outgoing' ? edge.targetId : edge.sourceId // 保持方向语义与 A → B 引用事实一致。
      if (visited.has(nextId)) continue // 已在更短或相同层级发现时不重复展示。
      visited.add(nextId) // 固化首次到达的最短层级。
      const nextDepth = current.depth + 1 // 计算下一层深度。
      const members = levels.get(nextDepth) || [] // 读取该层已有节点。
      members.push(nextId) // 将节点加入对应层级。
      levels.set(nextDepth, members) // 写回层级集合。
      edgeIds.add(citationEdgeId(edge)) // 记录实际经过的真实引用边。
      queue.push({ nodeId: nextId, depth: nextDepth }) // 继续探索更深层关系。
    }
  }
  return { levels: [...levels.entries()].sort(([left], [right]) => left - right).map(([depth, nodeIds]) => ({ depth, nodeIds: [...nodeIds].sort((left, right) => left.localeCompare(right, 'en')) })), edgeIds: [...edgeIds].sort((left, right) => left.localeCompare(right, 'en')) } // 返回稳定层级和边集合。
}

/** 收集某论文沿出边可达的前置引用工作。 */
export function collectCitationAncestors(nodeId: string, nodes: CitationAnalysisNode[], edges: VisualEdge[], maxDepth: number): CitationNeighborhood { // 保持 ancestors 仅为图结构命名。
  return collectCitationNeighborhood(nodeId, nodes, edges, maxDepth, 'outgoing') // A → B 表示 A 引用了 B，故前置工作沿出边。
}

/** 收集某论文沿入边可达的后续引用论文。 */
export function collectCitationDescendants(nodeId: string, nodes: CitationAnalysisNode[], edges: VisualEdge[], maxDepth: number): CitationNeighborhood { // 返回被引链下游论文。
  return collectCitationNeighborhood(nodeId, nodes, edges, maxDepth, 'incoming') // 引用当前论文的后续工作位于其入边来源端。
}

/** 对 API 图响应应用年份、来源和局部原始度数筛选，且不修改输入。 */
export function filterCitationGraphData(graph: CitationGraphData, filters: CitationGraphFilter): CitationGraphFilterResult { // 返回后端契约兼容的前端筛选图。
  const originalNodes = graph.nodes.map((node) => ({ ...node })) // 复制节点防止修改父组件响应。
  const originalEdges = graph.edges.map((edge) => ({ ...edge })) // 复制边防止修改父组件响应。
  const nodeIds = new Set(originalNodes.map((node) => node.paper_id)) // 限定当前搜索结果快照中的节点范围。
  const inDegree = new Map<string, number>() // 统计当前结果集内部原始引用入度。
  const outDegree = new Map<string, number>() // 统计当前结果集内部原始引用出度。
  for (const nodeId of nodeIds) { inDegree.set(nodeId, 0); outDegree.set(nodeId, 0) } // 初始化所有节点的局部度数。
  for (const edge of originalEdges) if (edge.edge_type === 'cites' && nodeIds.has(edge.source_paper_id) && nodeIds.has(edge.target_paper_id) && edge.source_paper_id !== edge.target_paper_id) { inDegree.set(edge.target_paper_id, (inDegree.get(edge.target_paper_id) || 0) + 1); outDegree.set(edge.source_paper_id, (outDegree.get(edge.source_paper_id) || 0) + 1) } // 只以保存的真实引用计算局部度数。
  const sources = filters.sources ? new Set(filters.sources) : null // 规范化可选来源白名单。
  const visibleNodes = originalNodes.filter((node) => { // 对每篇当前结果集论文应用事实型条件。
    if (filters.yearStart !== null && filters.yearStart !== undefined && (node.year === null || node.year < filters.yearStart)) return false // 缺失或早于起始年份时隐藏。
    if (filters.yearEnd !== null && filters.yearEnd !== undefined && (node.year === null || node.year > filters.yearEnd)) return false // 缺失或晚于结束年份时隐藏。
    if (sources && !sources.has(node.source)) return false // 来源不在用户选择范围时隐藏。
    if ((inDegree.get(node.paper_id) || 0) < Math.max(0, filters.minimumInDegree || 0)) return false // 使用当前结果集内部原始入度筛选。
    if ((outDegree.get(node.paper_id) || 0) < Math.max(0, filters.minimumOutDegree || 0)) return false // 使用当前结果集内部原始出度筛选。
    if (filters.includeIsolates === false && (inDegree.get(node.paper_id) || 0) === 0 && (outDegree.get(node.paper_id) || 0) === 0) return false // 用户要求隐藏孤立论文时过滤。
    return true // 满足全部事实条件时保留。
  })
  const visibleIds = new Set(visibleNodes.map((node) => node.paper_id)) // 收集筛选后仍可见的端点。
  const visibleEdges = originalEdges.filter((edge) => visibleIds.has(edge.source_paper_id) && visibleIds.has(edge.target_paper_id)) // 删除任一端点被隐藏的关系。
  return { graph: { ...graph, nodes: visibleNodes, edges: visibleEdges }, visibleNodeCount: visibleNodes.length, totalNodeCount: originalNodes.length } // 保持后端响应其余字段不变。
}

/** 计算当前可见图的内部结构指标，绝不解释为全局学术影响力。 */
export function analyzeCitationGraph(nodes: CitationAnalysisNode[], edges: VisualEdge[]): CitationGraphMetrics { // 返回当前范围内可审计的局部统计。
  const adjacency = buildCitationAdjacency(nodes, edges) // 复用统一真实引用索引。
  const inDegree = new Map<string, number>(nodes.map((node) => [node.id, 0])) // 初始化当前可见节点入度。
  const outDegree = new Map<string, number>(nodes.map((node) => [node.id, 0])) // 初始化当前可见节点出度。
  for (const edge of adjacency.edges) { inDegree.set(edge.targetId, (inDegree.get(edge.targetId) || 0) + 1); outDegree.set(edge.sourceId, (outDegree.get(edge.sourceId) || 0) + 1) } // 仅统计真实内部引用边。
  const undirected = new Map<string, Set<string>>(nodes.map((node) => [node.id, new Set<string>()])) // 为弱连通分量建立无向邻接。
  for (const edge of adjacency.edges) { undirected.get(edge.sourceId)?.add(edge.targetId); undirected.get(edge.targetId)?.add(edge.sourceId) } // 引用方向不影响弱连通归属。
  const visited = new Set<string>() // 记录已归入某弱连通分量的节点。
  let componentCount = 0 // 记录当前可见图的弱连通分量数。
  for (const nodeId of [...adjacency.nodeIds].sort((left, right) => left.localeCompare(right, 'en'))) { if (visited.has(nodeId)) continue; componentCount += 1; const queue = [nodeId]; visited.add(nodeId); for (let cursor = 0; cursor < queue.length; cursor += 1) for (const nextId of undirected.get(queue[cursor]) || []) if (!visited.has(nextId)) { visited.add(nextId); queue.push(nextId) } } // 用稳定 BFS 覆盖每个分量。
  const maxIn = Math.max(0, ...inDegree.values()) // 读取当前结果集内部最大入度。
  const maxOut = Math.max(0, ...outDegree.values()) // 读取当前结果集内部最大出度。
  const years = nodes.map((node) => node.year).filter((year): year is number => typeof year === 'number' && Number.isFinite(year)) // 仅收集可信年份。
  return { nodeCount: nodes.length, citationEdgeCount: adjacency.edges.length, componentCount, isolateCount: nodes.filter((node) => (inDegree.get(node.id) || 0) === 0 && (outDegree.get(node.id) || 0) === 0).length, maxInDegreeNodeIds: maxIn ? nodes.filter((node) => (inDegree.get(node.id) || 0) === maxIn).map((node) => node.id).sort((left, right) => left.localeCompare(right, 'en')) : [], maxOutDegreeNodeIds: maxOut ? nodes.filter((node) => (outDegree.get(node.id) || 0) === maxOut).map((node) => node.id).sort((left, right) => left.localeCompare(right, 'en')) : [], earliestYear: years.length ? Math.min(...years) : null, latestYear: years.length ? Math.max(...years) : null } // 返回局部指标并明确空图安全值。
}
