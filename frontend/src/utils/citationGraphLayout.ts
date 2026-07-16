/** 计算不依赖随机力导向的时间分层引用图布局。 */

export type CitationEdgeType = 'cites' | 'same_work' // 限制前端只识别后端已审计的两类关系。
export type CitationViewMode = 'backbone' | 'full' // 明确区分降噪研究主干与完整事实网络视图。

export interface CitationGraphApiNode { // 描述后端引用图接口返回的最小论文节点。
  paper_id: string // 保存稳定论文标识。
  title: string // 保存可展示论文标题。
  year: number | null // 保存来源提供的发表年份。
  relevance: number | null // 保存已有相关性分数而不重新推断。
  source: string // 保存主学术来源名称。
  work_family_id?: string | null // 保存可选版本族事实标识。
}

export interface CitationGraphApiEdge { // 描述后端返回的受限事实关系边。
  source_paper_id: string // 表示引用方或版本族链的起点。
  target_paper_id: string // 表示被引方或版本族链的终点。
  edge_type: CitationEdgeType // 区分真实引用与版本族关系。
}

export interface CitationGraphData { // 表示引用图接口完整响应中布局需要的字段。
  nodes: CitationGraphApiNode[] // 保存已裁剪的原始论文节点。
  edges: CitationGraphApiEdge[] // 保存仅来自已保存快照的事实关系。
  truncated: boolean // 回显后端是否按节点上限裁剪。
  max_nodes: number // 回显后端实际采用的节点上限。
}

export interface CitationLayoutOptions { // 描述纯布局函数的可控展示选项。
  width: number // 指定当前 SVG 的逻辑宽度。
  collapseFamilies: boolean // 指定是否默认把同一版本族合并为一个节点。
  includeVersionLinks: boolean // 指定展开版本族时是否绘制可关闭的黄色虚线。
  focusNodeId?: string | null // 指定仅查看某论文的一阶邻域。
  priorityNodeId?: string | null // 指定需要优先保留直接关系的当前选中论文。
  includeIsolates: boolean // 指定是否将孤立节点加入底部网格。
  viewMode?: CitationViewMode // 指定研究主干或完整网络，缺省保持研究主干。
  maxVisibleEdges?: number // 指定研究主干中可展示真实引用边的全图上限。
  maxOutgoingEdgesPerNode?: number // 指定研究主干中单论文可展示真实引用出边上限。
}

export interface CitationLayoutNode { // 描述可直接交给 D3 渲染的稳定坐标节点。
  id: string // 保存视觉节点稳定标识，版本族聚合后可能不同于单篇论文标识。
  paperIds: string[] // 保存该视觉节点涵盖的一篇或多篇论文标识。
  title: string // 保存用于节点标签和侧栏的代表标题。
  year: number | null // 保存用于时间列的最早可信年份。
  source: string // 保存单来源或多个版本来源的紧凑说明。
  relevance: number | null // 保存聚合后最高相关性。
  familyId: string | null // 保存被合并的版本族标识。
  memberCount: number // 保存聚合版本数以便节点显示。
  x: number // 保存稳定横轴坐标。
  y: number // 保存稳定纵轴坐标。
  radius: number // 保存按入度对数缩放后的节点半径。
  inDegree: number // 保存当前可见真实引用入度。
  outDegree: number // 保存当前可见真实引用出度。
  displayInDegree: number // 保存当前视图实际展示的真实引用入度。
  displayOutDegree: number // 保存当前视图实际展示的真实引用出度。
  community: number // 保存弱连通分量编号作为颜色分类。
  isIsolate: boolean // 标记无入边无出边的默认折叠论文。
  showLabel: boolean // 标记是否永久显示短标题。
  labelText: string // 保存经语义缩放后的短标题文本。
  labelBox: LayoutRect // 保存经候选代价选择后的实际标签占位矩形。
}

export interface CitationLayoutEdge { // 描述可直接绘制为 SVG 路径的关系边。
  id: string // 保存关系的稳定去重键。
  sourceId: string // 保存视觉起点节点标识。
  targetId: string // 保存视觉终点节点标识。
  edgeType: CitationEdgeType // 保持引用与版本族的视觉边界。
  path: string // 保存确定性的平滑三次贝塞尔路径。
  points: LayoutPoint[] // 保存贝塞尔端点和控制点，供测试与箭头末端切线使用。
  sourcePort: NodePortName // 保存动态选择的源节点连接端口。
  targetPort: NodePortName // 保存动态选择的目标节点连接端口。
}

export interface LayoutPoint { // 表示正交路由中的一个稳定二维坐标点。
  x: number // 保存横坐标。
  y: number // 保存纵坐标。
}

export interface LayoutRect { // 表示节点、标签或年份标题的可避让矩形。
  x: number // 保存左上角横坐标。
  y: number // 保存左上角纵坐标。
  width: number // 保存矩形宽度。
  height: number // 保存矩形高度。
}

export type NodePortName = 'left' | 'right' | 'top' | 'bottom' // 限制节点可选的四个圆周连接端口。

export interface CitationGraphLayout { // 描述布局模块输出给 D3 组件的完整可视状态。
  width: number // 回显布局逻辑宽度。
  height: number // 返回按分量和孤立网格扩展后的逻辑高度。
  nodes: CitationLayoutNode[] // 返回当前主图及可选孤立节点。
  edges: CitationLayoutEdge[] // 返回当前可见节点间的关系路径。
  isolatedCount: number // 返回默认折叠的孤立论文数量。
  mergedVersionNodeCount: number // 返回默认合并到版本族节点的额外论文数量。
  componentCount: number // 返回弱连通引用分支数量。
  yearTicks: number[] // 返回用于绘制稳定时间列的可信年份。
  originalCitationEdgeCount: number // 返回当前节点范围内的完整真实引用边数量。
  visibleCitationEdgeCount: number // 返回当前视图实际绘制的真实引用边数量。
  hiddenCitationEdgeCount: number // 返回研究主干因视觉筛选隐藏的真实引用边数量。
}

export interface VisualSeed { // 表示版本族合并后、尚未计算坐标的内部节点。
  id: string // 保存视觉节点标识。
  paperIds: string[] // 保存该节点包含的论文标识。
  title: string // 保存稳定代表标题。
  year: number | null // 保存最早可信发表年份。
  source: string // 保存来源摘要。
  relevance: number | null // 保存最高相关性。
  familyId: string | null // 保存可选版本族标识。
  memberCount: number // 保存成员数量。
}

export interface VisualEdge { // 表示映射到视觉节点后的内部关系边。
  sourceId: string // 保存视觉起点标识。
  targetId: string // 保存视觉终点标识。
  edgeType: CitationEdgeType // 保存事实关系类型。
}

const HORIZONTAL_MARGIN = 212 // 为最左和最右年份的外侧标题保留完整空间。
const TOP_MARGIN = 58 // 为年份刻度和图例保留顶部空间。
const COMPONENT_GAP = 84 // 让不同引用分支形成明确的垂直留白。
const NODE_ROW_GAP = 72 // 为同一年多个节点和同年弧线保留不重叠行距。
const ISOLATE_GRID_GAP = 112 // 为底部孤立论文网格保留稳定单元间距。
const ARROW_CLEARANCE = 8 // 让箭头尖端停在目标圆周外，避免被节点覆盖。
const SAME_YEAR_LANE_GAP = 22 // 让同年份多条引用边使用彼此独立的弧线路径。

/** 将空值和异常年份统一为未知，防止时间轴出现不可信列。 */
function normalizeYear(year: number | null | undefined): number | null { // 接收 API 模型中的可选年份。
  return typeof year === 'number' && Number.isInteger(year) && year >= 1800 && year <= 2100 ? year : null // 仅保留领域模型允许的可信年份。
}

/** 为同年论文和版本族代表选择可重复的稳定排序。 */
function compareSeed(left: VisualSeed, right: VisualSeed): number { // 比较两个未经坐标计算的视觉节点。
  return (left.year ?? Number.MAX_SAFE_INTEGER) - (right.year ?? Number.MAX_SAFE_INTEGER) || left.title.localeCompare(right.title, 'en') || left.id.localeCompare(right.id, 'en') // 按年份、标题和标识稳定排序。
}

/** 将同一版本族收敛为一个工作节点，并保留成员论文标识供侧栏使用。 */
function buildVisualSeeds(nodes: CitationGraphApiNode[], collapseFamilies: boolean): { seeds: VisualSeed[], paperToVisualId: Map<string, string> } { // 返回聚合节点和论文到视觉节点的映射。
  const groups = new Map<string, CitationGraphApiNode[]>() // 按视觉键汇集原始论文。
  const paperToVisualId = new Map<string, string>() // 建立原始论文标识到视觉节点的稳定映射。
  for (const node of nodes) { // 遍历后端返回的每篇已保存论文。
    const familyId = String(node.work_family_id || '').trim() // 规范化可选版本族标识。
    const visualId = collapseFamilies && familyId ? `family:${familyId}` : `paper:${node.paper_id}` // 默认仅合并具有明确版本族事实的论文。
    const members = groups.get(visualId) || [] // 读取该视觉节点已有成员或初始化空数组。
    members.push(node) // 将当前论文加入对应视觉节点。
    groups.set(visualId, members) // 写回版本族或单论文分组。
    paperToVisualId.set(node.paper_id, visualId) // 记录边映射和详情侧栏所需的转换关系。
  }
  const seeds = [...groups.entries()].map(([id, members]) => { // 将每个稳定分组投影为可布局节点。
    const orderedMembers = [...members].sort((left, right) => (normalizeYear(left.year) ?? Number.MAX_SAFE_INTEGER) - (normalizeYear(right.year) ?? Number.MAX_SAFE_INTEGER) || left.title.localeCompare(right.title, 'en') || left.paper_id.localeCompare(right.paper_id, 'en')) // 选择最早且稳定的代表论文。
    const years = orderedMembers.map((member) => normalizeYear(member.year)).filter((year): year is number => year !== null) // 收集所有可信发表年份。
    const sources = [...new Set(orderedMembers.map((member) => member.source).filter(Boolean))] // 收集版本族成员的来源，避免伪造单一来源。
    const scores = orderedMembers.map((member) => member.relevance).filter((score): score is number => typeof score === 'number') // 收集已有相关性分数，不做新计算。
    const familyId = collapseFamilies ? String(orderedMembers[0]?.work_family_id || '').trim() || null : null // 仅在合并模式下把族标识暴露给视觉节点。
    return { id, paperIds: orderedMembers.map((member) => member.paper_id), title: orderedMembers[0]?.title || '未命名论文', year: years.length ? Math.min(...years) : null, source: sources.length > 1 ? '多个来源' : sources[0] || '未知来源', relevance: scores.length ? Math.max(...scores) : null, familyId, memberCount: orderedMembers.length } // 返回不含坐标的聚合事实。
  }).sort(compareSeed) // 保持分组输出顺序跨渲染稳定。
  return { seeds, paperToVisualId } // 返回供边映射和布局复用的两个结果。
}

/** 仅保留可映射到当前视觉节点的事实边，并删除合并后形成的内部自环。 */
function buildVisualEdges(edges: CitationGraphApiEdge[], paperToVisualId: Map<string, string>, collapseFamilies: boolean, includeVersionLinks: boolean): VisualEdge[] { // 将原始论文边转换为视觉节点边。
  const seen = new Set<string>() // 防止同一合并边重复绘制。
  const visualEdges: VisualEdge[] = [] // 累积通过过滤的可视关系。
  for (const edge of edges) { // 逐条处理后端已审计关系。
    if (edge.edge_type === 'same_work' && (!includeVersionLinks || collapseFamilies)) continue // 合并版本族时不再绘制内部版本关系，展开后才允许显示黄色虚线。
    const sourceId = paperToVisualId.get(edge.source_paper_id) // 将论文起点映射到视觉节点。
    const targetId = paperToVisualId.get(edge.target_paper_id) // 将论文终点映射到视觉节点。
    if (!sourceId || !targetId || sourceId === targetId) continue // 排除图外论文和版本族合并造成的内部自环。
    const key = `${edge.edge_type}:${sourceId}:${targetId}` // 生成稳定去重键。
    if (seen.has(key)) continue // 跳过合并后完全相同的重复关系。
    seen.add(key) // 标记当前边已保留。
    visualEdges.push({ sourceId, targetId, edgeType: edge.edge_type }) // 保持后端事实类型，不生成关键词或模型推断边。
  }
  return visualEdges // 返回过滤后的视觉关系集合。
}

export interface BackboneEdgeOptions { // 描述研究主干筛选的确定性视觉裁剪边界。
  priorityNodeId?: string | null // 指定选中或聚焦论文对应的视觉节点标识。
  maxVisibleEdges?: number // 指定全图最多保留多少条真实引用边。
  maxOutgoingEdgesPerNode?: number // 指定每个引用方最多保留多少条真实引用出边。
}

export interface BackboneEdgeSelection { // 描述研究主干筛选输出，不改变任何原始关系事实。
  visibleEdges: VisualEdge[] // 保存当前实际需要绘制的真实引用边。
  hiddenEdgeCount: number // 保存因传递约简或显示上限隐藏的真实引用边数量。
}

const DEFAULT_MAX_VISIBLE_EDGES = 28 // 为默认研究主干设置可读的全图真实引用边上限。
const DEFAULT_MAX_OUTGOING_EDGES = 3 // 为默认研究主干设置单篇论文可展示的真实引用出边上限。

/** 生成稳定关系键，避免输入数组顺序影响筛选和路径方向。 */
function visualEdgeId(edge: VisualEdge): string { // 接收已映射到视觉节点的事实边。
  return `${edge.edgeType}:${edge.sourceId}:${edge.targetId}` // 使用边类型和两端稳定标识构成唯一键。
}

/** 使用确定性 Tarjan 遍历标识强连通分量，供主干约简保守处理循环关系。 */
function findStrongComponents(nodes: VisualSeed[], edges: VisualEdge[]): Map<string, number> { // 返回节点到稳定强连通分量编号的映射。
  const adjacency = new Map<string, string[]>() // 建立仅含真实引用关系的有向邻接表。
  for (const node of nodes) adjacency.set(node.id, []) // 先为全部节点创建空邻接数组。
  for (const edge of edges) adjacency.get(edge.sourceId)?.push(edge.targetId) // 写入真实引用方向，不推断反向关系。
  for (const targets of adjacency.values()) targets.sort((left, right) => left.localeCompare(right, 'en')) // 固定邻接访问顺序，避免结果随输入抖动。
  const indices = new Map<string, number>() // 保存每个节点首次访问序号。
  const lowLinks = new Map<string, number>() // 保存 Tarjan 回溯时的最小可达序号。
  const stack: string[] = [] // 保存当前深度优先搜索路径。
  const inStack = new Set<string>() // 快速判断节点是否仍位于当前搜索栈。
  const components = new Map<string, number>() // 累积最终节点到分量编号的映射。
  let visitIndex = 0 // 记录下一个确定性访问序号。
  let componentIndex = 0 // 记录下一个确定性分量编号。
  const visit = (nodeId: string): void => { // 深度遍历当前节点并在必要时收敛一个强连通分量。
    indices.set(nodeId, visitIndex) // 写入当前节点首次访问序号。
    lowLinks.set(nodeId, visitIndex) // 初始低链接等于首次访问序号。
    visitIndex += 1 // 为下一节点递增序号。
    stack.push(nodeId) // 将当前节点压入活动搜索栈。
    inStack.add(nodeId) // 标记当前节点仍可参与本分量回边。
    for (const targetId of adjacency.get(nodeId) || []) { // 依稳定顺序扩展全部真实引用邻居。
      if (!indices.has(targetId)) { // 尚未访问的邻居继续深度遍历。
        visit(targetId) // 递归处理该邻居。
        lowLinks.set(nodeId, Math.min(lowLinks.get(nodeId) || 0, lowLinks.get(targetId) || 0)) // 汇总子树能够回到的最低序号。
      } else if (inStack.has(targetId)) { // 栈内邻居表示当前分量的有效回边。
        lowLinks.set(nodeId, Math.min(lowLinks.get(nodeId) || 0, indices.get(targetId) || 0)) // 用回边起点更新最低序号。
      }
    }
    if (lowLinks.get(nodeId) !== indices.get(nodeId)) return // 非分量根节点等待上层收敛。
    while (stack.length) { // 从栈顶依次弹出当前强连通分量成员。
      const memberId = stack.pop() as string // 当前循环已确认栈非空，安全读取成员标识。
      inStack.delete(memberId) // 成员离开活动搜索栈。
      components.set(memberId, componentIndex) // 为成员写入同一个分量编号。
      if (memberId === nodeId) break // 弹到当前分量根节点时停止。
    }
    componentIndex += 1 // 为下一个分量预留编号。
  }
  for (const node of [...nodes].sort(compareSeed)) if (!indices.has(node.id)) visit(node.id) // 从稳定节点顺序启动所有未访问分量。
  return components // 返回循环安全的强连通分量映射。
}

/** 在排除指定边后检查是否仍有一条真实引用路径可达目标，用于安全隐藏传递边。 */
function hasAlternativeCitationPath(edges: VisualEdge[], sourceId: string, targetId: string, excludedEdgeId: string): boolean { // 只检查当前暂时保留的事实边集合。
  const adjacency = new Map<string, string[]>() // 构造排除候选边后的确定性邻接表。
  for (const edge of edges) { // 遍历当前仍可显示的真实引用边。
    if (visualEdgeId(edge) === excludedEdgeId) continue // 删除候选边后才验证替代路径。
    const targets = adjacency.get(edge.sourceId) || [] // 读取来源节点已有相邻目标。
    targets.push(edge.targetId) // 加入当前可用的事实方向。
    adjacency.set(edge.sourceId, targets) // 写回邻接表。
  }
  for (const targets of adjacency.values()) targets.sort((left, right) => left.localeCompare(right, 'en')) // 固定遍历顺序以确保输出稳定。
  const visited = new Set<string>([sourceId]) // 防止循环关系造成无限遍历。
  const queue = [sourceId] // 从候选边源节点开始广度搜索。
  for (let cursor = 0; cursor < queue.length; cursor += 1) { // 使用游标遍历，避免移除队首带来的额外开销。
    const currentId = queue[cursor] // 读取当前待扩展节点。
    for (const nextId of adjacency.get(currentId) || []) { // 访问所有当前可用的直接引用目标。
      if (nextId === targetId) return true // 找到不使用候选边的替代事实路径。
      if (visited.has(nextId)) continue // 已访问节点无需重复排队。
      visited.add(nextId) // 标记节点已访问以安全处理循环。
      queue.push(nextId) // 继续搜索后续事实边。
    }
  }
  return false // 没有替代路径时必须保留候选边。
}

/** 计算仅供视觉裁剪使用的稳定显示优先级，绝不表示论文的学术重要性。 */
function compareDisplayPriority(left: VisualEdge, right: VisualEdge, nodeById: Map<string, VisualSeed>, incomingDegree: Map<string, number>, priorityNodeId: string | null | undefined): number { // 比较两条真实引用边的展示顺序。
  const priorityDifference = Number(right.sourceId === priorityNodeId || right.targetId === priorityNodeId) - Number(left.sourceId === priorityNodeId || left.targetId === priorityNodeId) // 优先保留选中或聚焦论文的直接事实关系。
  if (priorityDifference) return priorityDifference // 有直接关系优先级差异时无需比较后续条件。
  const leftTarget = nodeById.get(left.targetId) // 读取左边目标节点的已有事实元数据。
  const rightTarget = nodeById.get(right.targetId) // 读取右边目标节点的已有事实元数据。
  const degreeDifference = (incomingDegree.get(right.targetId) || 0) - (incomingDegree.get(left.targetId) || 0) // 优先保留当前可见事实图中被更多论文引用的目标。
  if (degreeDifference) return degreeDifference // 被引数不同可直接确定顺序。
  const relevanceDifference = (rightTarget?.relevance ?? Number.NEGATIVE_INFINITY) - (leftTarget?.relevance ?? Number.NEGATIVE_INFINITY) // 其次复用已有相关性，不重新推断。
  if (relevanceDifference) return relevanceDifference // 相关性不同可直接确定顺序。
  const leftDistance = Math.abs((nodeById.get(left.sourceId)?.year ?? 0) - (leftTarget?.year ?? 0)) // 计算左边两端的已知年份距离。
  const rightDistance = Math.abs((nodeById.get(right.sourceId)?.year ?? 0) - (rightTarget?.year ?? 0)) // 计算右边两端的已知年份距离。
  if (leftDistance !== rightDistance) return leftDistance - rightDistance // 年份更接近的边优先，降低长边视觉负担。
  return visualEdgeId(left).localeCompare(visualEdgeId(right), 'en') // 所有可审计指标相同时按稳定边键收敛。
}

/** 选择研究主干真实引用边：保守传递约简后再应用节点和全图显示上限。 */
export function selectBackboneEdges(nodes: VisualSeed[], edges: VisualEdge[], options: BackboneEdgeOptions = {}): BackboneEdgeSelection { // 返回不改变原始事实的视觉筛选结果。
  const citationById = new Map<string, VisualEdge>() // 先按稳定键去重，防止异常输入造成重复绘制。
  for (const edge of edges) { // 只遍历调用方给出的视觉关系。
    if (edge.edgeType !== 'cites' || edge.sourceId === edge.targetId) continue // 主干只处理真实引用，且布局本就不接受自环。
    citationById.set(visualEdgeId(edge), { ...edge }) // 复制边对象，保证输入数组和对象均不被原地修改。
  }
  const originalEdges = [...citationById.values()].sort((left, right) => visualEdgeId(left).localeCompare(visualEdgeId(right), 'en')) // 固定全流程的关系处理顺序。
  if (!originalEdges.length) return { visibleEdges: [], hiddenEdgeCount: 0 } // 空图无需继续计算强连通分量或显示上限。
  const nodeById = new Map(nodes.map((node) => [node.id, node])) // 索引现有可审计节点元数据。
  const incomingDegree = new Map<string, number>() // 计算完整事实图中的目标入度，供显示优先级复用。
  for (const edge of originalEdges) incomingDegree.set(edge.targetId, (incomingDegree.get(edge.targetId) || 0) + 1) // 只统计真实引用边，不混入版本族虚线。
  const strongComponents = findStrongComponents(nodes, originalEdges) // 明确识别循环分量，避免在其中做激进传递约简。
  const priorityNodeId = options.priorityNodeId || null // 规范化可选选中或聚焦节点标识。
  const isProtected = (edge: VisualEdge): boolean => edge.sourceId === priorityNodeId || edge.targetId === priorityNodeId // 选中或聚焦论文的直接事实关系不被主干筛选隐藏。
  let retainedEdges = [...originalEdges] // 从完整事实边开始逐步执行保守筛选。
  for (const candidate of originalEdges) { // 按稳定关系键尝试删除可由现有路径表达的跨分量边。
    const sameComponent = strongComponents.get(candidate.sourceId) === strongComponents.get(candidate.targetId) // 循环分量内部边必须保守保留。
    if (sameComponent || isProtected(candidate)) continue // 不约简循环内部关系或当前用户关注的直接关系。
    if (hasAlternativeCitationPath(retainedEdges, candidate.sourceId, candidate.targetId, visualEdgeId(candidate))) retainedEdges = retainedEdges.filter((edge) => visualEdgeId(edge) !== visualEdgeId(candidate)) // 仅在仍有替代路径时隐藏传递冗余边。
  }
  const maxOutgoing = Math.max(1, Math.floor(options.maxOutgoingEdgesPerNode ?? DEFAULT_MAX_OUTGOING_EDGES)) // 规范化每个来源节点的显示上限。
  const afterOutgoingLimit: VisualEdge[] = [] // 累积单节点上限后的事实边。
  const edgesBySource = new Map<string, VisualEdge[]>() // 按引用方分组，以限制每篇论文同时展示的出边。
  for (const edge of retainedEdges) { // 遍历传递约简后仍保留的边。
    const grouped = edgesBySource.get(edge.sourceId) || [] // 读取当前引用方已有边集合。
    grouped.push(edge) // 将当前边加入同一个引用方。
    edgesBySource.set(edge.sourceId, grouped) // 写回来源节点分组。
  }
  for (const sourceId of [...edgesBySource.keys()].sort((left, right) => left.localeCompare(right, 'en'))) { // 固定来源节点处理顺序。
    const grouped = edgesBySource.get(sourceId) || [] // 读取该论文的所有候选出边。
    const protectedEdges = grouped.filter(isProtected) // 当前选中或聚焦论文的直接关系必须优先保留。
    const rankedEdges = grouped.filter((edge) => !isProtected(edge)).sort((left, right) => compareDisplayPriority(left, right, nodeById, incomingDegree, priorityNodeId)) // 其余边按可审计显示优先级排序。
    afterOutgoingLimit.push(...protectedEdges, ...rankedEdges.slice(0, Math.max(0, maxOutgoing - protectedEdges.length))) // 保护关系可突破普通上限，避免用户选中论文失去直接关系。
  }
  const maxVisible = Math.max(1, Math.floor(options.maxVisibleEdges ?? DEFAULT_MAX_VISIBLE_EDGES)) // 规范化全图显示上限。
  const protectedEdges = afterOutgoingLimit.filter(isProtected).sort((left, right) => compareDisplayPriority(left, right, nodeById, incomingDegree, priorityNodeId)) // 先排序并保留所有当前用户关注的直接关系。
  const rankedEdges = afterOutgoingLimit.filter((edge) => !isProtected(edge)).sort((left, right) => compareDisplayPriority(left, right, nodeById, incomingDegree, priorityNodeId)) // 再排序普通候选边。
  const visibleEdges = [...protectedEdges, ...rankedEdges.slice(0, Math.max(0, maxVisible - protectedEdges.length))].sort((left, right) => visualEdgeId(left).localeCompare(visualEdgeId(right), 'en')) // 最终按稳定键输出，保证路径与渲染顺序不跳动。
  return { visibleEdges, hiddenEdgeCount: originalEdges.length - visibleEdges.length } // 回显被主干隐藏的原始真实引用数量。
}

/** 计算仅由真实引用边构成的弱连通分量，版本族关系不合并引用分支。 */
function findWeakComponents(nodes: VisualSeed[], edges: VisualEdge[]): Map<string, number> { // 返回视觉节点到分量编号的映射。
  const adjacency = new Map<string, Set<string>>() // 以无向邻接表表达弱连通关系。
  for (const node of nodes) adjacency.set(node.id, new Set()) // 先为孤立论文创建空集合。
  for (const edge of edges.filter((edge) => edge.edgeType === 'cites')) { // 仅使用真实引用边判定分支。
    adjacency.get(edge.sourceId)?.add(edge.targetId) // 加入从引用方到被引方的弱连接。
    adjacency.get(edge.targetId)?.add(edge.sourceId) // 反向加入以便忽略方向计算连通性。
  }
  const components = new Map<string, number>() // 保存最终组件编号。
  let componentIndex = 0 // 记录下一个可用的稳定分量编号。
  for (const node of [...nodes].sort(compareSeed)) { // 以稳定节点顺序启动深度遍历。
    if (components.has(node.id)) continue // 已被前序遍历覆盖时无需重复处理。
    const queue = [node.id] // 初始化当前分量的广度遍历队列。
    components.set(node.id, componentIndex) // 标记种子节点归属。
    for (let cursor = 0; cursor < queue.length; cursor += 1) { // 使用索引遍历避免移除数组头部带来的额外开销。
      const currentId = queue[cursor] // 读取当前待扩展节点。
      for (const neighborId of adjacency.get(currentId) || []) { // 遍历当前节点的无向相邻论文。
        if (components.has(neighborId)) continue // 已访问邻居不应再次加入队列。
        components.set(neighborId, componentIndex) // 写入相同分量编号。
        queue.push(neighborId) // 排队继续扩展该引用分支。
      }
    }
    componentIndex += 1 // 当前分量遍历完成后递增编号。
  }
  return components // 返回完整、稳定的弱连通分量映射。
}

/** 在每个引用分支先选代表论文，再应用全图上限以减少默认常驻标签。 */
function selectPersistentLabelIds(nodes: CitationLayoutNode[], maxLabels: number, maxLabelsPerComponent: number): Set<string> { // 返回稳定且可测试的默认标签节点标识。
  const byComponent = new Map<number, CitationLayoutNode[]>() // 按弱连通分支组织非孤立论文。
  for (const node of nodes.filter((item) => !item.isIsolate)) { // 孤立论文默认不占用主图标签预算。
    const members = byComponent.get(node.community) || [] // 读取当前分支已有候选。
    members.push(node) // 将节点加入其可审计引用分支。
    byComponent.set(node.community, members) // 写回分组。
  }
  const candidates: CitationLayoutNode[] = [] // 累积各分支的少量代表节点。
  for (const component of [...byComponent.keys()].sort((left, right) => left - right)) { // 固定分支顺序以保证刷新稳定。
    const members = byComponent.get(component) || [] // 读取该分支成员。
    members.sort((left, right) => (right.inDegree - left.inDegree) || (right.relevance ?? Number.NEGATIVE_INFINITY) - (left.relevance ?? Number.NEGATIVE_INFINITY) || left.id.localeCompare(right.id, 'en')) // 优先被引用更多、已有相关性更高的论文。
    candidates.push(...members.slice(0, maxLabelsPerComponent)) // 每个分支最多提供有限常驻标签候选。
  }
  candidates.sort((left, right) => (right.inDegree - left.inDegree) || (right.relevance ?? Number.NEGATIVE_INFINITY) - (left.relevance ?? Number.NEGATIVE_INFINITY) || left.community - right.community || left.id.localeCompare(right.id, 'en')) // 全图再以同一稳定规则裁剪。
  return new Set(candidates.slice(0, maxLabels).map((node) => node.id)) // 返回受全图预算约束的默认标签集合。
}

/** 按相邻节点的纵向重心重排同一年节点，降低跨年份引用边的交叉概率。 */
export function minimizeLayerCrossings(buckets: Map<number | null, VisualSeed[]>, componentEdges: VisualEdge[]): void { // 仅改变同年节点的稳定纵向顺序。
  const years = [...buckets.keys()].sort((left, right) => (left ?? Number.MIN_SAFE_INTEGER) - (right ?? Number.MIN_SAFE_INTEGER)) // 固定从旧到新的扫描顺序。
  for (const bucket of buckets.values()) bucket.sort(compareSeed) // 在没有邻居信息时保留标题和标识的稳定顺序。
  for (let round = 0; round < 3; round += 1) { // 进行有限轮双向重心扫描，避免引入随机布局。
    for (const direction of [years, [...years].reverse()]) { // 正反两个方向都吸收相邻年份的排序信息。
      const rankByNodeId = new Map<string, number>() // 保存本轮开始时各节点在同年列中的相对行号。
      for (const year of years) (buckets.get(year) || []).forEach((seed, index) => rankByNodeId.set(seed.id, index)) // 写入稳定的初始相对位置。
      for (const year of direction) { // 逐列按邻接节点重心重排。
        const bucket = buckets.get(year) || [] // 读取当前年份列。
        bucket.sort((left, right) => { // 同一年只比较可审计引用邻居的重心。
          const barycenter = (seed: VisualSeed): number => { // 计算该节点所有跨年真实引用邻居的平均相对行号。
            const neighborRanks = componentEdges // 只读取当前弱连通分支内的已保存关系。
              .filter((edge) => edge.edgeType === 'cites' && (edge.sourceId === seed.id || edge.targetId === seed.id)) // 忽略版本族辅助关系和无关节点。
              .map((edge) => rankByNodeId.get(edge.sourceId === seed.id ? edge.targetId : edge.sourceId)) // 取得另一端节点的当前行号。
              .filter((rank): rank is number => rank !== undefined) // 排除当前分支外或不可见节点。
            return neighborRanks.length ? neighborRanks.reduce((sum, rank) => sum + rank, 0) / neighborRanks.length : rankByNodeId.get(seed.id) || 0 // 无邻居时保持原相对位置。
          }
          return barycenter(left) - barycenter(right) || compareSeed(left, right) // 重心相同仍使用稳定排序避免刷新抖动。
        })
      }
    }
  }
}

/** 以实际出现年份的序数而非年份差值分配等间距时间列。 */
export function assignYearColumns(years: Array<number | null>, width: number): Map<number | null, number> { // 返回每个可见年份列的稳定横坐标。
  const orderedYears = [...new Set(years)].sort((left, right) => (left ?? Number.MAX_SAFE_INTEGER) - (right ?? Number.MAX_SAFE_INTEGER)) // 保证缺失年份不会在画布中制造空白列。
  const outerPadding = Math.min(96, Math.max(48, width * 0.1)) // 仅保留响应式边界，不为某个年份或标题写死空间。
  const span = Math.max(1, width - outerPadding * 2) // 计算可用的序数布局宽度。
  const columns = new Map<number | null, number>() // 初始化年份到横坐标的映射。
  orderedYears.forEach((year, index) => columns.set(year, orderedYears.length <= 1 ? width / 2 : outerPadding + span * index / (orderedYears.length - 1))) // 为实际列均分可用空间。
  return columns // 返回给节点、通道和年份轴共同使用。
}

/** 将长标题缩放为默认标签可读的长度，完整标题仍保留在节点提示和详情中。 */
export function shortenCitationLabel(title: string, maxLength = 24): string { // 统一纯布局和组件使用的标签文本规则。
  return title.length > maxLength ? `${title.slice(0, maxLength).trim()}…` : title // 在空间不足时优先减少默认常驻标签，而不改变论文事实。
}

/** 以中英文字符宽度估算可用于碰撞检测的标签矩形尺寸。 */
export function measureLabelBoxes(nodes: CitationLayoutNode[]): Map<string, LayoutRect> { // 返回每个节点默认短标题的可靠估算尺寸。
  const boxes = new Map<string, LayoutRect>() // 保存不含位置的宽高信息，位置由候选选择阶段填充。
  for (const node of nodes) { // 遍历当前可见节点。
    const text = shortenCitationLabel(node.title) // 使用与实际渲染完全一致的标签文本。
    const textWidth = [...text].reduce((sum, character) => sum + (/[\u0000-\u00ff]/.test(character) ? 6.4 : 10.8), 0) // 按拉丁和宽字符分别估算十一像素字体宽度。
    boxes.set(node.id, { x: 0, y: 0, width: Math.min(220, Math.max(58, textWidth + 16)), height: 20 }) // 加入左右内边距并限制极长标签占用。
  }
  return boxes // 供候选标签布局和最终渲染共同使用。
}

/** 判断两个矩形是否有面积重叠。 */
function rectanglesOverlap(left: LayoutRect, right: LayoutRect): boolean { // 使用轴对齐矩形快速判定障碍相交。
  return left.x < right.x + right.width && left.x + left.width > right.x && left.y < right.y + right.height && left.y + left.height > right.y // 只有横纵方向均相交才视为碰撞。
}

/** 将圆形节点转为保守的可避让矩形。 */
function nodeObstacle(node: CitationLayoutNode): LayoutRect { // 让正交边和标签统一面对矩形障碍。
  return { x: node.x - node.radius - 3, y: node.y - node.radius - 3, width: (node.radius + 3) * 2, height: (node.radius + 3) * 2 } // 为描边和端口留出额外三像素安全区。
}

/** 判断水平或垂直线段是否穿过障碍矩形。 */
function segmentHitsRect(start: LayoutPoint, end: LayoutPoint, rect: LayoutRect): boolean { // 仅处理本布局产生的正交路径线段。
  if (start.x === end.x) return start.x > rect.x && start.x < rect.x + rect.width && Math.max(Math.min(start.y, end.y), rect.y) < Math.min(Math.max(start.y, end.y), rect.y + rect.height) // 判断竖线段与矩形内部是否相交。
  if (start.y === end.y) return start.y > rect.y && start.y < rect.y + rect.height && Math.max(Math.min(start.x, end.x), rect.x) < Math.min(Math.max(start.x, end.x), rect.x + rect.width) // 判断横线段与矩形内部是否相交。
  return false // 非正交段不由本路由器生成。
}

/** 为一个节点生成左右、上下和四个斜向的标签候选矩形。 */
function labelCandidates(node: CitationLayoutNode, metrics: LayoutRect): LayoutRect[] { // 返回八个可比较位置。
  const gap = 8 // 让标签与圆周和端口保持可读间隔。
  const left = node.x - node.radius - gap - metrics.width // 计算左侧候选横坐标。
  const right = node.x + node.radius + gap // 计算右侧候选横坐标。
  const top = node.y - node.radius - gap - metrics.height // 计算上侧候选纵坐标。
  const bottom = node.y + node.radius + gap // 计算下侧候选纵坐标。
  const middleX = node.x - metrics.width / 2 // 计算正上和正下候选横坐标。
  const middleY = node.y - metrics.height / 2 // 计算正左和正右候选纵坐标。
  return [ // 按贴近节点的四向和四角顺序返回所有候选。
    { x: left, y: middleY, width: metrics.width, height: metrics.height }, { x: right, y: middleY, width: metrics.width, height: metrics.height }, { x: middleX, y: top, width: metrics.width, height: metrics.height }, { x: middleX, y: bottom, width: metrics.width, height: metrics.height }, { x: left, y: top, width: metrics.width, height: metrics.height }, { x: right, y: top, width: metrics.width, height: metrics.height }, { x: left, y: bottom, width: metrics.width, height: metrics.height }, { x: right, y: bottom, width: metrics.width, height: metrics.height },
  ]
}

/** 基于标签、节点、预估边、画布和年份标题障碍选择最低代价标签位置。 */
export function chooseLabelPositions(nodes: CitationLayoutNode[], metricsById: Map<string, LayoutRect>, edgeSegments: Array<[LayoutPoint, LayoutPoint]>, canvas: LayoutRect, yearTitleBoxes: LayoutRect[]): Map<string, LayoutRect> { // 返回每个节点实际占位标签矩形。
  const chosen = new Map<string, LayoutRect>() // 逐个写入已确定的标签位置。
  const orderedNodes = [...nodes].sort((left, right) => Number(right.showLabel) - Number(left.showLabel) || (right.inDegree + right.outDegree) - (left.inDegree + left.outDegree) || left.id.localeCompare(right.id, 'en')) // 优先为默认可见和重要节点保留位置。
  for (const node of orderedNodes) { // 对每个节点评估八个标签候选。
    const metrics = metricsById.get(node.id) || { x: 0, y: 0, width: 80, height: 20 } // 为异常标题提供安全回退尺寸。
    let best = labelCandidates(node, metrics)[0] // 初始化为第一个候选以保证总有确定输出。
    let bestCost = Number.POSITIVE_INFINITY // 初始化最低代价。
    for (const candidate of labelCandidates(node, metrics)) { // 比较各候选位置的综合代价。
      const outside = Math.max(0, canvas.x - candidate.x) + Math.max(0, candidate.y - canvas.y) + Math.max(0, candidate.x + candidate.width - (canvas.x + canvas.width)) + Math.max(0, candidate.y + candidate.height - (canvas.y + canvas.height)) // 计算超出画布的距离。
      const labelHits = [...chosen.values()].filter((box) => rectanglesOverlap(candidate, box)).length // 统计与已选择标签重叠次数。
      const nodeHits = nodes.filter((other) => other.id !== node.id && rectanglesOverlap(candidate, nodeObstacle(other))).length // 统计遮挡其他节点次数。
      const yearHits = yearTitleBoxes.filter((box) => rectanglesOverlap(candidate, box)).length // 统计遮挡年份标题次数。
      const edgeHits = edgeSegments.filter(([start, end]) => segmentHitsRect(start, end, candidate)).length // 统计预估引用边穿过标签次数。
      const distance = Math.hypot(candidate.x + candidate.width / 2 - node.x, candidate.y + candidate.height / 2 - node.y) // 计算标签距离节点的代价。
      const cost = outside * 900 + labelHits * 9000 + nodeHits * 6500 + yearHits * 2600 + edgeHits * 1800 + distance * 2 // 按可读性优先级组合各项成本。
      if (cost < bestCost) { best = candidate; bestCost = cost } // 选择代价最低且稳定的候选。
    }
    chosen.set(node.id, best) // 固化当前节点标签，供后续节点避让。
  }
  return chosen // 返回所有标签的最终矩形。
}

/** 返回节点给定端口在圆周外的连接点，目标端口可额外留出箭头安全间距。 */
function portPoint(node: CitationLayoutNode, port: NodePortName, extra = 2): LayoutPoint { // 将离散端口投影到圆周外。
  const offset = node.radius + extra // 统一计算圆周与额外间距。
  if (port === 'left') return { x: node.x - offset, y: node.y } // 返回左端口。
  if (port === 'right') return { x: node.x + offset, y: node.y } // 返回右端口。
  if (port === 'top') return { x: node.x, y: node.y - offset } // 返回上端口。
  return { x: node.x, y: node.y + offset } // 返回下端口。
}

/** 为每条边动态选择端口，并同时考虑长度、标签避让和端口占用。 */
export function assignNodePorts(nodes: CitationLayoutNode[], edges: VisualEdge[], labelBoxes: Map<string, LayoutRect>): Map<string, { sourcePort: NodePortName, targetPort: NodePortName }> { // 返回按稳定边标识索引的端口分配。
  const nodeById = new Map(nodes.map((node) => [node.id, node])) // 建立节点索引。
  const occupied = new Map<string, number>() // 统计每个节点端口已分配的边数量。
  const assignments = new Map<string, { sourcePort: NodePortName, targetPort: NodePortName }>() // 保存最终端口选择。
  const ports: NodePortName[] = ['left', 'right', 'top', 'bottom'] // 声明四个可选圆周端口。
  for (const edge of [...edges].sort((left, right) => `${left.sourceId}:${left.targetId}`.localeCompare(`${right.sourceId}:${right.targetId}`, 'en'))) { // 按稳定顺序处理，避免端口随刷新跳变。
    const source = nodeById.get(edge.sourceId) // 读取源节点。
    const target = nodeById.get(edge.targetId) // 读取目标节点。
    if (!source || !target) continue // 隐藏节点不参与端口选择。
    const edgeId = `${edge.edgeType}:${edge.sourceId}:${edge.targetId}` // 构造稳定映射键。
    let best = { sourcePort: 'left' as NodePortName, targetPort: 'right' as NodePortName } // 初始化可用默认值。
    let bestCost = Number.POSITIVE_INFINITY // 初始化最低代价。
    for (const sourcePort of ports) for (const targetPort of ports) { // 枚举十六种端口组合。
      const start = portPoint(source, sourcePort) // 计算源端口坐标。
      const end = portPoint(target, targetPort, ARROW_CLEARANCE) // 计算带箭头间距的目标端口坐标。
      const labelHits = [...labelBoxes.entries()].filter(([id, box]) => id !== source.id && id !== target.id && (segmentHitsRect(start, { x: end.x, y: start.y }, box) || segmentHitsRect({ x: end.x, y: start.y }, end, box))).length // 用两段正交预览估算标签避让。
      const targetDirection = target.x === source.x ? 0 : Math.sign(target.x - source.x) // 计算跨年边的横向目标方向。
      const sourceDirectionPenalty = targetDirection && (sourcePort === (targetDirection > 0 ? 'left' : 'right')) ? 90 : 0 // 惩罚背离目标方向的源端口。
      const targetDirectionPenalty = targetDirection && (targetPort === (targetDirection > 0 ? 'right' : 'left')) ? 90 : 0 // 惩罚背离来源方向的目标端口。
      const occupancy = (occupied.get(`${source.id}:${sourcePort}`) || 0) + (occupied.get(`${target.id}:${targetPort}`) || 0) // 汇总两端端口当前占用。
      const cost = Math.abs(start.x - end.x) + Math.abs(start.y - end.y) + labelHits * 1400 + occupancy * 72 + sourceDirectionPenalty + targetDirectionPenalty // 组合路径长度、标签和端口拥挤成本。
      if (cost < bestCost) { best = { sourcePort, targetPort }; bestCost = cost } // 选择最低代价端口组合。
    }
    assignments.set(edgeId, best) // 保存当前边端口。
    occupied.set(`${source.id}:${best.sourcePort}`, (occupied.get(`${source.id}:${best.sourcePort}`) || 0) + 1) // 增加源端口占用。
    occupied.set(`${target.id}:${best.targetPort}`, (occupied.get(`${target.id}:${best.targetPort}`) || 0) + 1) // 增加目标端口占用。
  }
  return assignments // 返回供通道分配和最终路由使用的端口方案。
}

interface EdgeLane { // 描述边在年份通道或同年外部通道中的稳定轨迹。
  kind: 'cross_year' | 'same_year' // 区分两类路由空间。
  coordinate: number // 保存跨年水平车道的纵坐标或同年外部车道的横坐标。
  channelXs: number[] // 保存跨年边依次经过的年份间通道中心。
}

/** 计算一条正交折线路径与障碍矩形的碰撞数量。 */
function countPathHits(points: LayoutPoint[], obstacles: LayoutRect[]): number { // 用于从候选车道中选择碰撞最少的路径。
  let hits = 0 // 初始化碰撞计数。
  for (let index = 1; index < points.length; index += 1) hits += obstacles.filter((obstacle) => segmentHitsRect(points[index - 1], points[index], obstacle)).length // 累加每段与所有障碍的碰撞。
  return hits // 返回总碰撞数量。
}

/** 判断两条正交线段是否在非端点位置相交。 */
function segmentsCross(leftStart: LayoutPoint, leftEnd: LayoutPoint, rightStart: LayoutPoint, rightEnd: LayoutPoint): boolean { // 用于避免不同引用边在通道内交叉。
  const leftVertical = leftStart.x === leftEnd.x // 判断第一条线段方向。
  const rightVertical = rightStart.x === rightEnd.x // 判断第二条线段方向。
  if (leftVertical === rightVertical) return false // 共向线段由独立车道分配避免，不按交叉处理。
  const verticalStart = leftVertical ? leftStart : rightStart // 读取竖线段起点。
  const verticalEnd = leftVertical ? leftEnd : rightEnd // 读取竖线段终点。
  const horizontalStart = leftVertical ? rightStart : leftStart // 读取横线段起点。
  const horizontalEnd = leftVertical ? rightEnd : leftEnd // 读取横线段终点。
  return verticalStart.x > Math.min(horizontalStart.x, horizontalEnd.x) && verticalStart.x < Math.max(horizontalStart.x, horizontalEnd.x) && horizontalStart.y > Math.min(verticalStart.y, verticalEnd.y) && horizontalStart.y < Math.max(verticalStart.y, verticalEnd.y) // 仅统计真正穿过而非共享端点的交叉。
}

/** 统计候选路径与已经分配路线的交叉次数。 */
function countPathCrossings(points: LayoutPoint[], occupiedSegments: Array<[LayoutPoint, LayoutPoint]>): number { // 为后续边的通道选择加入边交叉代价。
  let crossings = 0 // 初始化交叉计数。
  for (let index = 1; index < points.length; index += 1) for (const [start, end] of occupiedSegments) if (segmentsCross(points[index - 1], points[index], start, end)) crossings += 1 // 比较当前每段与所有既有路段。
  return crossings // 返回总交叉数量。
}

/** 根据端口和候选车道构造跨年份的分段通道路径。 */
function crossYearPoints(start: LayoutPoint, end: LayoutPoint, channelXs: number[], laneY: number): LayoutPoint[] { // 长距离边必须逐个经过年份间通道。
  const firstChannel = channelXs[0] ?? (start.x + end.x) / 2 // 相邻年份也至少使用一个内部通道。
  const lastChannel = channelXs[channelXs.length - 1] ?? firstChannel // 读取最后一个通道位置。
  return [start, { x: firstChannel, y: start.y }, { x: firstChannel, y: laneY }, ...channelXs.slice(1, -1).map((x) => ({ x, y: laneY })), { x: lastChannel, y: laneY }, { x: lastChannel, y: end.y }, end] // 将长边拆分为逐通道的正交段。
}

/** 根据端口和候选外部车道构造同年份的单侧绕行路径。 */
function sameYearPoints(start: LayoutPoint, end: LayoutPoint, laneX: number): LayoutPoint[] { // 同年边只在选定外部通道内绕行。
  return [start, { x: laneX, y: start.y }, { x: laneX, y: end.y }, end] // 保持两端最后一段垂直或水平，令箭头方向可读。
}

/** 为跨年和同年边分配可避让标签、节点和年份标题的动态车道。 */
export function allocateEdgeLanes(nodes: CitationLayoutNode[], edges: VisualEdge[], ports: Map<string, { sourcePort: NodePortName, targetPort: NodePortName }>, yearColumns: Map<number | null, number>, labelBoxes: Map<string, LayoutRect>, canvas: LayoutRect, yearTitleBoxes: LayoutRect[]): Map<string, EdgeLane> { // 返回按稳定边标识索引的车道分配。
  const nodeById = new Map(nodes.map((node) => [node.id, node])) // 建立节点索引。
  const orderedColumns = [...yearColumns.entries()].sort((left, right) => left[1] - right[1]) // 按实际序数位置排列年份列。
  const columnIndex = new Map(orderedColumns.map(([year], index) => [year, index])) // 建立年份到列序号映射。
  const obstacles = [...nodes.map(nodeObstacle), ...labelBoxes.values(), ...yearTitleBoxes] // 将节点、标签和年份标题统一作为硬障碍。
  const lanes = new Map<string, EdgeLane>() // 累积每条边的车道。
  const sameYearUsage = new Map<string, number>() // 分别记录同一年左右外部通道的已占用轨道数。
  const occupiedSegments: Array<[LayoutPoint, LayoutPoint]> = [] // 保存先前已分配边的路段以减少后续交叉。
  for (const edge of [...edges].sort((left, right) => `${left.sourceId}:${left.targetId}`.localeCompare(`${right.sourceId}:${right.targetId}`, 'en'))) { // 按稳定顺序分配车道避免刷新跳变。
    const source = nodeById.get(edge.sourceId) // 读取源节点。
    const target = nodeById.get(edge.targetId) // 读取目标节点。
    const edgeId = `${edge.edgeType}:${edge.sourceId}:${edge.targetId}` // 构造稳定边标识。
    const assignment = ports.get(edgeId) // 读取前一阶段选择的端口。
    if (!source || !target || !assignment) continue // 隐藏节点或无法分配端口时跳过。
    const start = portPoint(source, assignment.sourcePort) // 读取源圆周端口。
    const end = portPoint(target, assignment.targetPort, ARROW_CLEARANCE) // 读取目标端口并预留箭头间距。
    if (source.year === target.year) { // 同年份关系动态选择左或右外部通道。
      const candidates: Array<{ laneX: number, key: string, points: LayoutPoint[] }> = [] // 收集左右多个外部轨道候选。
      for (const direction of [-1, 1]) { // 同时评估年份列左侧和右侧，绝不固定单侧。
        const side = direction < 0 ? 'left' : 'right' // 生成可读通道名称。
        const usageKey = `${source.year ?? 'unknown'}:${side}` // 按年份列和左右方向分别统计占用。
        const base = Math.max(source.radius, target.radius) + 28 + (sameYearUsage.get(usageKey) || 0) * SAME_YEAR_LANE_GAP // 根据已有轨道数向外递增。
        const laneX = source.x + direction * base // 计算该候选同年外部通道横坐标。
        candidates.push({ laneX, key: usageKey, points: sameYearPoints(start, end, laneX) }) // 记录该侧候选路径。
      }
      candidates.sort((left, right) => (countPathHits(left.points, obstacles) * 1800 + countPathCrossings(left.points, occupiedSegments) * 1100) - (countPathHits(right.points, obstacles) * 1800 + countPathCrossings(right.points, occupiedSegments) * 1100) || Math.abs(left.laneX - source.x) - Math.abs(right.laneX - source.x)) // 优先选择不碰撞、不交叉且更短的外部通道。
      const chosen = candidates[0] // 读取最低代价通道。
      sameYearUsage.set(chosen.key, (sameYearUsage.get(chosen.key) || 0) + 1) // 为后续同年边预留下一条独立轨道。
      lanes.set(edgeId, { kind: 'same_year', coordinate: chosen.laneX, channelXs: [] }) // 保存同年动态车道。
      for (let index = 1; index < chosen.points.length; index += 1) occupiedSegments.push([chosen.points[index - 1], chosen.points[index]]) // 记录已占用通道路段以减少后续交叉。
      continue // 同年边无需继续计算跨年通道。
    }
    const sourceIndex = columnIndex.get(source.year) ?? 0 // 读取源年份列索引。
    const targetIndex = columnIndex.get(target.year) ?? 0 // 读取目标年份列索引。
    const low = Math.min(sourceIndex, targetIndex) // 获取区间左端列索引。
    const high = Math.max(sourceIndex, targetIndex) // 获取区间右端列索引。
    const channelXs = Array.from({ length: Math.max(1, high - low) }, (_, index) => (orderedColumns[low + index][1] + orderedColumns[low + index + 1][1]) / 2) // 为每个跨越的年份间隔创建独立通道。
    const baseY = (start.y + end.y) / 2 // 使用两端中点作为首选跨年车道高度。
    const laneCandidates = [0, -1, 1, -2, 2, -3, 3, -4, 4].map((offset) => Math.max(canvas.y + 34, Math.min(canvas.y + canvas.height - 26, baseY + offset * 24))) // 生成上下多条候选通道车道。
    laneCandidates.sort((left, right) => (countPathHits(crossYearPoints(start, end, channelXs, left), obstacles) * 1800 + countPathCrossings(crossYearPoints(start, end, channelXs, left), occupiedSegments) * 1100) - (countPathHits(crossYearPoints(start, end, channelXs, right), obstacles) * 1800 + countPathCrossings(crossYearPoints(start, end, channelXs, right), occupiedSegments) * 1100) || Math.abs(left - baseY) - Math.abs(right - baseY)) // 优先选择不碰撞、不交叉且接近直达路径的车道。
    const chosenY = laneCandidates[0] // 读取最低综合代价的跨年车道。
    const chosenPoints = crossYearPoints(start, end, channelXs, chosenY) // 生成用于占用记录的实际分段路径。
    lanes.set(edgeId, { kind: 'cross_year', coordinate: chosenY, channelXs }) // 保存跨年分段通道车道。
    for (let index = 1; index < chosenPoints.length; index += 1) occupiedSegments.push([chosenPoints[index - 1], chosenPoints[index]]) // 将跨年通道路段加入后续边的交叉代价。
  }
  return lanes // 返回所有边的动态通道分配。
}

/** 去除重复和共线点，让正交路径在不改变末端切线的前提下更紧凑。 */
function simplifyPoints(points: LayoutPoint[]): LayoutPoint[] { // 保留通道折点和箭头末端方向。
  const unique = points.filter((point, index) => index === 0 || point.x !== points[index - 1].x || point.y !== points[index - 1].y) // 删除连续重复点。
  return unique.filter((point, index) => index === 0 || index === unique.length - 1 || !((unique[index - 1].x === point.x && point.x === unique[index + 1].x) || (unique[index - 1].y === point.y && point.y === unique[index + 1].y))) // 删除中间共线点但保留端点。
}

/** 将正交折线转为带圆角的 SVG 路径，并保留最终线段供箭头计算切线。 */
function roundedOrthogonalPath(points: LayoutPoint[]): string { // 将布局路由安全地投影为 SVG path。
  const compact = simplifyPoints(points) // 先消除无效折点。
  if (!compact.length) return '' // 空路径返回空字符串。
  let path = `M ${compact[0].x.toFixed(1)} ${compact[0].y.toFixed(1)}` // 从首端口开始。
  for (let index = 1; index < compact.length; index += 1) { // 逐段追加直线和圆角。
    const previous = compact[index - 1] // 读取上一点。
    const current = compact[index] // 读取当前折点或终点。
    const next = compact[index + 1] // 读取下一点以判断是否需要圆角。
    if (!next) { path += ` L ${current.x.toFixed(1)} ${current.y.toFixed(1)}`; continue } // 末段保持直线，marker 自动沿此方向旋转。
    const incoming = Math.min(9, Math.abs(current.x - previous.x) / 2 + Math.abs(current.y - previous.y) / 2) // 计算进入折点的圆角截断距离。
    const outgoing = Math.min(9, Math.abs(next.x - current.x) / 2 + Math.abs(next.y - current.y) / 2) // 计算离开折点的圆角截断距离。
    const before = { x: current.x + Math.sign(previous.x - current.x) * incoming, y: current.y + Math.sign(previous.y - current.y) * incoming } // 计算圆角前的截断点。
    const after = { x: current.x + Math.sign(next.x - current.x) * outgoing, y: current.y + Math.sign(next.y - current.y) * outgoing } // 计算圆角后的截断点。
    path += ` L ${before.x.toFixed(1)} ${before.y.toFixed(1)} Q ${current.x.toFixed(1)} ${current.y.toFixed(1)} ${after.x.toFixed(1)} ${after.y.toFixed(1)}` // 使用二次曲线平滑当前正交折角。
  }
  return path // 返回可直接交给 SVG 的圆角分段路径。
}

/** 根据端口和动态车道输出最终避障路径；节点、标签和年份标题均已参与车道代价。 */
export function routeEdgesAroundObstacles(nodes: CitationLayoutNode[], edges: VisualEdge[], ports: Map<string, { sourcePort: NodePortName, targetPort: NodePortName }>, lanes: Map<string, EdgeLane>): CitationLayoutEdge[] { // 返回可渲染且可测试的最终边集合。
  const nodeById = new Map(nodes.map((node) => [node.id, node])) // 建立节点索引。
  const routed: CitationLayoutEdge[] = [] // 累积完成的边路径。
  for (const edge of edges) { // 按已有事实边顺序生成最终路径。
    const edgeId = `${edge.edgeType}:${edge.sourceId}:${edge.targetId}` // 构造稳定边标识。
    const source = nodeById.get(edge.sourceId) // 读取源节点。
    const target = nodeById.get(edge.targetId) // 读取目标节点。
    const assignment = ports.get(edgeId) // 读取端口方案。
    const lane = lanes.get(edgeId) // 读取车道方案。
    if (!source || !target || !assignment || !lane) continue // 仅绘制当前可见且已完整分配的边。
    const start = portPoint(source, assignment.sourcePort) // 从源圆周外端口开始。
    const end = portPoint(target, assignment.targetPort, ARROW_CLEARANCE) // 在目标圆周外停下，给箭头留出间距。
    const points = lane.kind === 'same_year' ? sameYearPoints(start, end, lane.coordinate) : crossYearPoints(start, end, lane.channelXs, lane.coordinate) // 按边类型生成同年或跨年通道路径。
    routed.push({ id: edgeId, sourceId: edge.sourceId, targetId: edge.targetId, edgeType: edge.edgeType, points: simplifyPoints(points), path: roundedOrthogonalPath(points), sourcePort: assignment.sourcePort, targetPort: assignment.targetPort }) // 保存路径与末端切线所需的实际点集。
  }
  return routed // 返回供 D3 渲染的所有路径。
}

/** 将稳定字符串映射为小整数，不使用随机数决定曲线朝向。 */
function stableEdgeHash(value: string): number { // 接收稳定边键或节点标识。
  let hash = 0 // 初始化可重复哈希值。
  for (const character of value) hash = (hash * 31 + character.charCodeAt(0)) >>> 0 // 以固定乘数累积每个字符编码。
  return hash // 返回无符号整数供曲线方向选择。
}

/** 根据有向连线的主要方向选择语义一致的圆周端口名称。 */
function portNameForDirection(dx: number, dy: number): NodePortName { // 接收从当前节点指向另一端的向量。
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? 'right' : 'left' // 横向占主导时沿时间轴方向连接。
  return dy >= 0 ? 'bottom' : 'top' // 同年或纵向关系使用上下端口。
}

/** 从节点圆周外沿给定方向生成贝塞尔端点，避免路径穿过起点和终点圆形。 */
function circleExitPoint(node: CitationLayoutNode, dx: number, dy: number, clearance: number): LayoutPoint { // 接收节点、方向和额外安全距离。
  const length = Math.hypot(dx, dy) || 1 // 避免异常重叠坐标导致除零。
  const distance = node.radius + clearance // 保持端点停在节点圆周之外。
  return { x: node.x + dx / length * distance, y: node.y + dy / length * distance } // 返回沿方向外移后的稳定端点。
}

/** 为同年和同年份区间边分配确定性小幅偏移，避免所有曲线共用同一通道。 */
function createBezierLaneOffsets(nodes: CitationLayoutNode[], edges: VisualEdge[]): Map<string, number> { // 返回每条边相对曲率的稳定偏移量。
  const nodeById = new Map(nodes.map((node) => [node.id, node])) // 建立节点索引以读取年份和位置。
  const grouped = new Map<string, VisualEdge[]>() // 按共享时间通道聚合曲线。
  for (const edge of edges) { // 遍历当前实际绘制的关系边。
    const source = nodeById.get(edge.sourceId) // 读取引用方节点。
    const target = nodeById.get(edge.targetId) // 读取被引方节点。
    if (!source || !target) continue // 不完整节点不应生成路径。
    const key = source.year === target.year ? `same:${source.year ?? 'unknown'}` : `cross:${source.year ?? 'unknown'}:${target.year ?? 'unknown'}` // 同年边共享外侧弧线通道，跨年边按年份对分组。
    const members = grouped.get(key) || [] // 读取该通道已有成员。
    members.push(edge) // 将当前边加入稳定分组。
    grouped.set(key, members) // 写回分组。
  }
  const offsets = new Map<string, number>() // 累积最终边键到曲率偏移的映射。
  for (const members of grouped.values()) { // 分别为每个通道分配交替的上下或左右偏移。
    members.sort((left, right) => visualEdgeId(left).localeCompare(visualEdgeId(right), 'en')) // 固定边顺序，避免刷新后车道跳动。
    members.forEach((edge, index) => { // 依序分配左右交替且逐步拉开的车道。
      const magnitude = 8 + Math.floor(index / 2) * 8 // 同一通道后续边仅小幅拉开，限制曲率增长。
      const sign = index % 2 === 0 ? 1 : -1 // 交替向曲线法向两侧展开。
      offsets.set(visualEdgeId(edge), sign * magnitude) // 保存当前边稳定偏移。
    })
  }
  return offsets // 返回供贝塞尔控制点计算使用的偏移。
}

/** 以确定性三次贝塞尔曲线替代默认正交回折路径，保持真实边的方向和端点。 */
export function routeEdgesAsBezierCurves(nodes: CitationLayoutNode[], edges: VisualEdge[]): CitationLayoutEdge[] { // 返回可直接由 SVG 渲染的平滑事实关系路径。
  const nodeById = new Map(nodes.map((node) => [node.id, node])) // 建立节点坐标索引。
  const laneOffsets = createBezierLaneOffsets(nodes, edges) // 先为共享通道中的边分配稳定小幅偏移。
  const routed: CitationLayoutEdge[] = [] // 累积完成的贝塞尔关系路径。
  for (const edge of [...edges].sort((left, right) => visualEdgeId(left).localeCompare(visualEdgeId(right), 'en'))) { // 固定路径输出顺序，避免 D3 刷新抖动。
    const source = nodeById.get(edge.sourceId) // 读取当前关系起点节点。
    const target = nodeById.get(edge.targetId) // 读取当前关系终点节点。
    if (!source || !target) continue // 隐藏或缺失节点不能形成有效路径。
    const dx = target.x - source.x // 计算从引用方到被引方的横向方向。
    const dy = target.y - source.y // 计算从引用方到被引方的纵向方向。
    const length = Math.hypot(dx, dy) || 1 // 保护同坐标异常输入，保证后续数值有限。
    const start = circleExitPoint(source, dx, dy, 3) // 从引用方圆周外开始曲线。
    const end = circleExitPoint(target, -dx, -dy, ARROW_CLEARANCE) // 在被引方圆周外结束，并为按需箭头保留空间。
    const normalX = -dy / length // 计算稳定单位法向量横向分量。
    const normalY = dx / length // 计算稳定单位法向量纵向分量。
    const direction = stableEdgeHash(visualEdgeId(edge)) % 2 === 0 ? 1 : -1 // 依据稳定边键交替曲线朝向，避免长边全挤在一侧。
    const sameYearExtra = source.year === target.year ? 18 : 0 // 同年关系使用额外独立弧线以离开年份列。
    const baseCurvature = Math.min(88, Math.max(24, length * 0.13 + sameYearExtra)) // 距离越远可适度增大弯曲，但始终限制上限。
    const curvature = direction * baseCurvature + (laneOffsets.get(visualEdgeId(edge)) || 0) // 叠加稳定车道偏移，让平行关系彼此分开。
    const controlOne = { x: start.x + (end.x - start.x) * 0.34 + normalX * curvature, y: start.y + (end.y - start.y) * 0.34 + normalY * curvature } // 将第一控制点放在起点方向前段并偏离法向。
    const controlTwo = { x: start.x + (end.x - start.x) * 0.66 + normalX * curvature, y: start.y + (end.y - start.y) * 0.66 + normalY * curvature } // 将第二控制点放在终点方向后段并使用相同通道偏移。
    const path = `M ${start.x.toFixed(1)} ${start.y.toFixed(1)} C ${controlOne.x.toFixed(1)} ${controlOne.y.toFixed(1)}, ${controlTwo.x.toFixed(1)} ${controlTwo.y.toFixed(1)}, ${end.x.toFixed(1)} ${end.y.toFixed(1)}` // 输出稳定、无回折的 SVG 三次贝塞尔命令。
    routed.push({ id: visualEdgeId(edge), sourceId: edge.sourceId, targetId: edge.targetId, edgeType: edge.edgeType, path, points: [start, controlOne, controlTwo, end], sourcePort: portNameForDirection(dx, dy), targetPort: portNameForDirection(-dx, -dy) }) // 保留端点、控制点和端口语义供测试与箭头渲染使用。
  }
  return routed // 返回当前可见事实关系的完整平滑路径。
}

/** 构造默认时间分层、分支分离且孤立节点折叠的完整引用图布局。 */
export function buildCitationGraphLayout(graph: CitationGraphData, options: CitationLayoutOptions): CitationGraphLayout { // 只使用后端已保存节点和事实边生成前端坐标。
  const width = Math.max(640, Math.round(options.width || 960)) // 限制过窄容器仍保留年份列的可读宽度。
  const viewMode = options.viewMode || 'backbone' // 默认进入研究主干，完整网络必须由用户显式切换。
  const { seeds, paperToVisualId } = buildVisualSeeds(graph.nodes, options.collapseFamilies) // 先按默认版本族合并策略生成视觉节点。
  const visualEdges = buildVisualEdges(graph.edges, paperToVisualId, options.collapseFamilies, options.includeVersionLinks) // 仅保留用户允许显示的事实关系。
  const visibleSeedIds = new Set(seeds.map((seed) => seed.id)) // 保存当前所有视觉节点标识供边过滤。
  const focusedVisualId = options.focusNodeId ? paperToVisualId.get(options.focusNodeId) : null // 将一阶邻域请求的论文标识转换为当前视觉节点标识。
  if (focusedVisualId && visibleSeedIds.has(focusedVisualId)) { // 仅在焦点节点实际位于当前图中时执行邻域过滤。
    const neighborhoodIds = new Set([focusedVisualId]) // 始终保留被点击的中心论文。
    for (const edge of visualEdges.filter((edge) => edge.edgeType === 'cites')) { // 仅用真实引用边扩展一阶邻域。
      if (edge.sourceId === focusedVisualId) neighborhoodIds.add(edge.targetId) // 保留该论文直接引用的论文。
      if (edge.targetId === focusedVisualId) neighborhoodIds.add(edge.sourceId) // 保留直接引用该论文的论文。
    }
    for (const seedId of [...visibleSeedIds]) if (!neighborhoodIds.has(seedId)) visibleSeedIds.delete(seedId) // 移除不属于一阶邻域的视觉节点。
  }
  const visibleSeeds = seeds.filter((seed) => visibleSeedIds.has(seed.id)) // 按当前全局或邻域范围保留节点。
  const originalVisibleEdges = visualEdges.filter((edge) => visibleSeedIds.has(edge.sourceId) && visibleSeedIds.has(edge.targetId)) // 保留当前范围内的完整事实关系，供原始度数和完整模式使用。
  const priorityVisualId = options.priorityNodeId ? paperToVisualId.get(options.priorityNodeId) || (seeds.some((seed) => seed.id === options.priorityNodeId) ? options.priorityNodeId : null) : focusedVisualId // 同时兼容论文标识和组件已选中的视觉节点标识。
  const originalCitationEdges = originalVisibleEdges.filter((edge) => edge.edgeType === 'cites') // 主干筛选只处理真实引用边。
  const backboneSelection = selectBackboneEdges(visibleSeeds, originalCitationEdges, { priorityNodeId: priorityVisualId, maxVisibleEdges: options.maxVisibleEdges, maxOutgoingEdgesPerNode: options.maxOutgoingEdgesPerNode }) // 使用纯函数筛选研究主干，不修改原始图数据。
  const visibleCitationEdges = viewMode === 'full' ? [...originalCitationEdges] : backboneSelection.visibleEdges // 完整网络无条件恢复当前范围内全部真实引用事实。
  const displayEdges = [...visibleCitationEdges, ...originalVisibleEdges.filter((edge) => edge.edgeType === 'same_work')] // 版本族虚线继续遵守既有开关，不参与主干筛选。
  const inDegree = new Map<string, number>(visibleSeeds.map((seed) => [seed.id, 0])) // 初始化当前可见图中的真实引用入度。
  const outDegree = new Map<string, number>(visibleSeeds.map((seed) => [seed.id, 0])) // 初始化当前可见图中的真实引用出度。
  const displayInDegree = new Map<string, number>(visibleSeeds.map((seed) => [seed.id, 0])) // 初始化当前视图实际绘制的真实引用入度。
  const displayOutDegree = new Map<string, number>(visibleSeeds.map((seed) => [seed.id, 0])) // 初始化当前视图实际绘制的真实引用出度。
  for (const edge of originalCitationEdges) { // 原始事实入出度不因主干视觉裁剪而改变。
    inDegree.set(edge.targetId, (inDegree.get(edge.targetId) || 0) + 1) // 被其他论文引用时增加目标入度。
    outDegree.set(edge.sourceId, (outDegree.get(edge.sourceId) || 0) + 1) // 当前论文引用其他论文时增加来源出度。
  }
  for (const edge of visibleCitationEdges) { // 单独统计当前视图真正绘制的真实引用关系。
    displayInDegree.set(edge.targetId, (displayInDegree.get(edge.targetId) || 0) + 1) // 记录当前显示入度。
    displayOutDegree.set(edge.sourceId, (displayOutDegree.get(edge.sourceId) || 0) + 1) // 记录当前显示出度。
  }
  const components = findWeakComponents(visibleSeeds, originalVisibleEdges) // 始终依据完整可见事实关系计算分支，避免主干裁剪改变节点社区归属。
  const isolateSeeds = visibleSeeds.filter((seed) => (inDegree.get(seed.id) || 0) === 0 && (outDegree.get(seed.id) || 0) === 0) // 识别不会帮助阅读引用主图的孤立论文。
  const mainSeeds = visibleSeeds.filter((seed) => !isolateSeeds.some((isolate) => isolate.id === seed.id)) // 默认从主图折叠孤立论文。
  const displayedSeeds = options.includeIsolates ? [...mainSeeds, ...isolateSeeds] : mainSeeds // 根据用户开关决定是否把孤立论文加入可见集合。
  const yearTicks = [...new Set(mainSeeds.map((seed) => seed.year).filter((year): year is number => year !== null))].sort((left, right) => left - right) // 仅用主图可信年份绘制时间列。
  const yearColumns = assignYearColumns(mainSeeds.map((seed) => seed.year), width) // 按实际出现年份等间距分配序数时间列。
  const xForYear = (year: number | null): number => yearColumns.get(year) ?? width / 2 // 未知年份或空图安全回退到画布中心。
  const componentGroups = new Map<number, VisualSeed[]>() // 按引用分支组织主图节点。
  for (const seed of mainSeeds) { // 仅为非孤立节点分配主图垂直空间。
    const component = components.get(seed.id) || 0 // 读取稳定分支编号。
    const members = componentGroups.get(component) || [] // 读取该分支已有节点。
    members.push(seed) // 加入当前引用分支。
    componentGroups.set(component, members) // 写回分支成员集合。
  }
  const sortedComponents = [...componentGroups.entries()].sort((left, right) => compareSeed([...left[1]].sort(compareSeed)[0], [...right[1]].sort(compareSeed)[0])) // 让引用分支按最早论文稳定自上而下排列。
  const positionedNodes: CitationLayoutNode[] = [] // 累积包含坐标、度数和颜色分支的主图节点。
  let cursorY = TOP_MARGIN + 44 // 从年份标签下方开始放置第一个引用分支。
  for (const [component, members] of sortedComponents) { // 逐个布置不同的弱连通引用分支。
    const buckets = new Map<number | null, VisualSeed[]>() // 按年份列组织该分支节点。
    for (const member of [...members].sort(compareSeed)) { // 按稳定顺序填充分支内年份桶。
      const bucket = buckets.get(member.year) || [] // 读取当前年份已有节点。
      bucket.push(member) // 将节点加入对应年份列。
      buckets.set(member.year, bucket) // 写回年份桶。
    }
    const memberIds = new Set(members.map((member) => member.id)) // 收集当前弱连通分支的节点标识。
    const componentEdges = originalVisibleEdges.filter((edge) => memberIds.has(edge.sourceId) && memberIds.has(edge.targetId)) // 继续使用完整事实边稳定降低同年节点交叉。
    minimizeLayerCrossings(buckets, componentEdges) // 按相邻年份节点重心稳定重排，尽量减少跨年边交叉。
    const largestBucket = Math.max(...[...buckets.values()].map((bucket) => bucket.length), 1) // 根据同年最大节点数预留分支高度。
    const componentHeight = Math.max(132, largestBucket * NODE_ROW_GAP + 44) // 保证小分支和同年密集分支均有可读空间。
    for (const [year, bucket] of buckets) { // 为分支内每个年份列分配稳定纵向行。
      bucket.sort(compareSeed).forEach((seed, index) => { // 在同年中按标题和标识稳定排列。
        const nodeInDegree = inDegree.get(seed.id) || 0 // 读取当前节点真实被引数量。
        const nodeOutDegree = outDegree.get(seed.id) || 0 // 读取当前节点真实引用数量。
        const radius = Math.min(22, 8 + Math.log1p(nodeInDegree) * 5) // 使用入度对数缩放，避免单节点过大。
        positionedNodes.push({ ...seed, x: xForYear(year), y: cursorY + 28 + index * NODE_ROW_GAP, radius, inDegree: nodeInDegree, outDegree: nodeOutDegree, displayInDegree: displayInDegree.get(seed.id) || 0, displayOutDegree: displayOutDegree.get(seed.id) || 0, community: component, isIsolate: false, showLabel: false, labelText: shortenCitationLabel(seed.title), labelBox: { x: 0, y: 0, width: 0, height: 0 } }) // 写入主图节点的完整稳定渲染数据。
      })
    }
    cursorY += componentHeight + COMPONENT_GAP // 在下一个引用分支前保留清晰留白。
  }
  if (options.includeIsolates && isolateSeeds.length) { // 用户手动展开时在主图下方添加规则网格。
    const columns = Math.max(2, Math.floor((width - 96) / 150)) // 按当前画布宽度计算孤立论文网格列数。
    const gridStartY = Math.max(cursorY, TOP_MARGIN + 120) // 避免孤立网格与最后一个引用分支重叠。
    isolateSeeds.sort(compareSeed).forEach((seed, index) => { // 使用年份和标题稳定排列孤立论文。
      const column = index % columns // 计算当前论文所在网格列。
      const row = Math.floor(index / columns) // 计算当前论文所在网格行。
      positionedNodes.push({ ...seed, x: 52 + column * ISOLATE_GRID_GAP, y: gridStartY + 44 + row * 74, radius: 8, inDegree: 0, outDegree: 0, displayInDegree: 0, displayOutDegree: 0, community: -1, isIsolate: true, showLabel: false, labelText: shortenCitationLabel(seed.title), labelBox: { x: 0, y: 0, width: 0, height: 0 } }) // 写入不参与引用社区颜色的孤立论文节点。
    })
    cursorY = gridStartY + Math.ceil(isolateSeeds.length / columns) * 74 + 74 // 扩展画布高度以完整容纳孤立网格。
  }
  const labelBudget = width < 760 ? 5 : 8 // 窄画布进一步收紧默认常驻标题数量。
  const importantLabelIds = selectPersistentLabelIds(positionedNodes, Math.min(labelBudget, mainSeeds.length), 2) // 每个引用分支最多保留两项代表标签，再应用全图上限。
  for (const node of positionedNodes) node.showLabel = importantLabelIds.has(node.id) // 其余论文仅在悬浮、选中或详情中显示标题。
  const nodeById = new Map(positionedNodes.map((node) => [node.id, node])) // 建立坐标索引供边路径生成。
  const height = Math.max(300, cursorY + 28) // 在标签和路由前固定当前画布有效高度。
  const canvas = { x: 0, y: 0, width, height } // 将画布边界作为标签和边路由的障碍约束。
  const yearTitleBoxes = [...yearColumns.entries()].filter(([year]) => year !== null).map(([year, x]) => ({ x: x - String(year).length * 3.5 - 5, y: 14, width: String(year).length * 7 + 10, height: 18 })) // 让标签和边避开年份轴标题文本。
  const provisionalSegments: Array<[LayoutPoint, LayoutPoint]> = [] // 用简化正交预览让标签选择提前考虑边位置。
  for (const edge of displayEdges) { // 为当前实际绘制的事实边建立简化预览，供标签候选避让使用。
    const source = nodeById.get(edge.sourceId) // 读取源节点。
    const target = nodeById.get(edge.targetId) // 读取目标节点。
    if (!source || !target) continue // 隐藏节点不进入标签代价。
    provisionalSegments.push([{ x: source.x, y: source.y }, { x: target.x, y: source.y }], [{ x: target.x, y: source.y }, { x: target.x, y: target.y }]) // 使用两段正交预览估算标签与边的相交。
  }
  const labelMetrics = measureLabelBoxes(positionedNodes) // 先测量实际短标题的宽高，避免仅在渲染阶段才处理文字。
  const allLabelBoxes = chooseLabelPositions(positionedNodes, labelMetrics, provisionalSegments, canvas, yearTitleBoxes) // 在八向候选中最小化标签、节点、边和年份标题冲突。
  for (const node of positionedNodes) node.labelBox = allLabelBoxes.get(node.id) || { x: node.x, y: node.y, width: 0, height: 0 } // 将纯函数选择的标签矩形写回渲染节点。
  const layoutEdges = routeEdgesAsBezierCurves(positionedNodes, displayEdges) // 默认使用平滑三次贝塞尔曲线，避免长边形成多次正交回折。
  const mergedVersionNodeCount = options.collapseFamilies ? seeds.reduce((count, seed) => count + Math.max(0, seed.memberCount - 1), 0) : 0 // 明确统计因版本族默认合并而未单独显示的论文节点。
  return { width, height, nodes: displayedSeeds.length ? positionedNodes : [], edges: layoutEdges, isolatedCount: isolateSeeds.length, mergedVersionNodeCount, componentCount: sortedComponents.length, yearTicks, originalCitationEdgeCount: originalCitationEdges.length, visibleCitationEdgeCount: visibleCitationEdges.length, hiddenCitationEdgeCount: viewMode === 'backbone' ? backboneSelection.hiddenEdgeCount : 0 } // 返回当前视图统计，避免组件重新遍历猜测。
}
