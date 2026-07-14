/** 计算不依赖随机力导向的时间分层引用图布局。 */

export type CitationEdgeType = 'cites' | 'same_work' // 限制前端只识别后端已审计的两类关系。

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
  includeIsolates: boolean // 指定是否将孤立节点加入底部网格。
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
  community: number // 保存弱连通分量编号作为颜色分类。
  isIsolate: boolean // 标记无入边无出边的默认折叠论文。
  showLabel: boolean // 标记是否永久显示短标题。
  labelSide: 'left' | 'right' // 保存标题相对节点的外侧方向，确保标题不进入引用边内部通道。
}

export interface CitationLayoutEdge { // 描述可直接绘制为 SVG 路径的关系边。
  id: string // 保存关系的稳定去重键。
  sourceId: string // 保存视觉起点节点标识。
  targetId: string // 保存视觉终点节点标识。
  edgeType: CitationEdgeType // 保持引用与版本族的视觉边界。
  path: string // 保存避开节点圆形、标题区域和箭头间距的贝塞尔路径。
}

export interface CitationGraphLayout { // 描述布局模块输出给 D3 组件的完整可视状态。
  width: number // 回显布局逻辑宽度。
  height: number // 返回按分量和孤立网格扩展后的逻辑高度。
  nodes: CitationLayoutNode[] // 返回当前主图及可选孤立节点。
  edges: CitationLayoutEdge[] // 返回当前可见节点间的关系路径。
  isolatedCount: number // 返回默认折叠的孤立论文数量。
  componentCount: number // 返回弱连通引用分支数量。
  yearTicks: number[] // 返回用于绘制稳定时间列的可信年份。
}

interface VisualSeed { // 表示版本族合并后、尚未计算坐标的内部节点。
  id: string // 保存视觉节点标识。
  paperIds: string[] // 保存该节点包含的论文标识。
  title: string // 保存稳定代表标题。
  year: number | null // 保存最早可信发表年份。
  source: string // 保存来源摘要。
  relevance: number | null // 保存最高相关性。
  familyId: string | null // 保存可选版本族标识。
  memberCount: number // 保存成员数量。
}

interface VisualEdge { // 表示映射到视觉节点后的内部关系边。
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

/** 按相邻节点的纵向重心重排同一年节点，降低跨年份引用边的交叉概率。 */
function orderBucketsByBarycenter(buckets: Map<number | null, VisualSeed[]>, componentEdges: VisualEdge[]): void { // 仅改变同年节点的稳定纵向顺序。
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

/** 生成只在年份列内部区域走线、连接圆周端口并为箭头留出间距的关系路径。 */
function buildEdgePath(source: CitationLayoutNode, target: CitationLayoutNode, edgeType: CitationEdgeType, sameYearLane: number): string { // 根据两个已定位节点和轨道路由生成 SVG 三次贝塞尔路径。
  const isSameYear = source.year === target.year && source.x === target.x // 同一年节点共享同一条时间列。
  if (isSameYear) { // 同年份引用需要沿年份线附近使用独立弧线。
    const internalDirection = source.labelSide === 'left' ? 1 : -1 // 始终向标题相反的内部区域绕行。
    const startX = source.x + internalDirection * (source.radius + 2) // 从源节点面向内部的圆周端口离开。
    const endX = target.x + internalDirection * (target.radius + ARROW_CLEARANCE) // 在目标圆周外预留箭头尖端间距。
    const laneX = source.x + internalDirection * (Math.max(source.radius, target.radius) + 26 + sameYearLane * SAME_YEAR_LANE_GAP) // 为多条同年边分配逐层外扩的独立轨道。
    return `M ${startX.toFixed(1)} ${source.y.toFixed(1)} C ${laneX.toFixed(1)} ${source.y.toFixed(1)} ${laneX.toFixed(1)} ${target.y.toFixed(1)} ${endX.toFixed(1)} ${target.y.toFixed(1)}` // 返回沿年份线附近的独立平滑弧线。
  }

  const horizontalDirection = target.x > source.x ? 1 : -1 // 确定两端相对年份列方向。
  const startX = source.x + horizontalDirection * (source.radius + 2) // 从源节点朝向目标的圆周左右端口离开。
  const endX = target.x - horizontalDirection * (target.radius + ARROW_CLEARANCE) // 在目标圆周前保留箭头和节点的安全间距。
  const horizontalDistance = Math.abs(endX - startX) // 计算内部年份区域可用的水平长度。
  const controlDistance = Math.min(92, Math.max(28, horizontalDistance * 0.38)) // 约束控制点距离以形成平缓且不过度弯折的曲线。
  const sourceControlX = startX + horizontalDirection * controlDistance // 让路径离开源节点时保持水平切线。
  const targetControlX = endX - horizontalDirection * controlDistance // 让路径进入目标节点时保持水平切线并令箭头方向明确。
  return `M ${startX.toFixed(1)} ${source.y.toFixed(1)} C ${sourceControlX.toFixed(1)} ${source.y.toFixed(1)} ${targetControlX.toFixed(1)} ${target.y.toFixed(1)} ${endX.toFixed(1)} ${target.y.toFixed(1)}` // 返回跨年份引用使用的平缓三次贝塞尔曲线。
}

/** 构造默认时间分层、分支分离且孤立节点折叠的完整引用图布局。 */
export function buildCitationGraphLayout(graph: CitationGraphData, options: CitationLayoutOptions): CitationGraphLayout { // 只使用后端已保存节点和事实边生成前端坐标。
  const width = Math.max(640, Math.round(options.width || 960)) // 限制过窄容器仍保留年份列的可读宽度。
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
  const visibleEdges = visualEdges.filter((edge) => visibleSeedIds.has(edge.sourceId) && visibleSeedIds.has(edge.targetId)) // 同步过滤两端均可见的关系。
  const inDegree = new Map<string, number>(visibleSeeds.map((seed) => [seed.id, 0])) // 初始化当前可见图中的真实引用入度。
  const outDegree = new Map<string, number>(visibleSeeds.map((seed) => [seed.id, 0])) // 初始化当前可见图中的真实引用出度。
  for (const edge of visibleEdges.filter((edge) => edge.edgeType === 'cites')) { // 只让真实引用影响节点重要性和孤立判断。
    inDegree.set(edge.targetId, (inDegree.get(edge.targetId) || 0) + 1) // 被其他论文引用时增加目标入度。
    outDegree.set(edge.sourceId, (outDegree.get(edge.sourceId) || 0) + 1) // 当前论文引用其他论文时增加来源出度。
  }
  const components = findWeakComponents(visibleSeeds, visibleEdges) // 计算仅由真实引用关系形成的弱连通分支。
  const isolateSeeds = visibleSeeds.filter((seed) => (inDegree.get(seed.id) || 0) === 0 && (outDegree.get(seed.id) || 0) === 0) // 识别不会帮助阅读引用主图的孤立论文。
  const mainSeeds = visibleSeeds.filter((seed) => !isolateSeeds.some((isolate) => isolate.id === seed.id)) // 默认从主图折叠孤立论文。
  const displayedSeeds = options.includeIsolates ? [...mainSeeds, ...isolateSeeds] : mainSeeds // 根据用户开关决定是否把孤立论文加入可见集合。
  const yearTicks = [...new Set(mainSeeds.map((seed) => seed.year).filter((year): year is number => year !== null))].sort((left, right) => left - right) // 仅用主图可信年份绘制时间列。
  const minYear = yearTicks[0] ?? null // 保存最早可信年份以确定横轴左端。
  const maxYear = yearTicks[yearTicks.length - 1] ?? null // 保存最新可信年份以确定横轴右端。
  const xForYear = (year: number | null): number => { // 将可信年份映射为稳定横轴坐标。
    if (year === null || minYear === null || maxYear === null) return HORIZONTAL_MARGIN // 未知年份固定放在最左侧的“年份未知”列。
    if (minYear === maxYear) return width / 2 // 同一年结果居中，避免除以零或全部贴边。
    return HORIZONTAL_MARGIN + ((year - minYear) / (maxYear - minYear)) * (width - HORIZONTAL_MARGIN * 2) // 保持旧论文在左、新论文在右的线性时间位置。
  }
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
    const componentEdges = visibleEdges.filter((edge) => memberIds.has(edge.sourceId) && memberIds.has(edge.targetId)) // 只将当前分支的事实边用于同年排序。
    orderBucketsByBarycenter(buckets, componentEdges) // 按相邻年份节点重心稳定重排，尽量减少跨年边交叉。
    const largestBucket = Math.max(...[...buckets.values()].map((bucket) => bucket.length), 1) // 根据同年最大节点数预留分支高度。
    const componentHeight = Math.max(132, largestBucket * NODE_ROW_GAP + 44) // 保证小分支和同年密集分支均有可读空间。
    for (const [year, bucket] of buckets) { // 为分支内每个年份列分配稳定纵向行。
      bucket.sort(compareSeed).forEach((seed, index) => { // 在同年中按标题和标识稳定排列。
        const nodeInDegree = inDegree.get(seed.id) || 0 // 读取当前节点真实被引数量。
        const nodeOutDegree = outDegree.get(seed.id) || 0 // 读取当前节点真实引用数量。
        const radius = Math.min(22, 8 + Math.log1p(nodeInDegree) * 5) // 使用入度对数缩放，避免单节点过大。
        positionedNodes.push({ ...seed, x: xForYear(year), y: cursorY + 28 + index * NODE_ROW_GAP, radius, inDegree: nodeInDegree, outDegree: nodeOutDegree, community: component, isIsolate: false, showLabel: false, labelSide: 'right' }) // 写入主图节点的完整稳定渲染数据。
      })
    }
    cursorY += componentHeight + COMPONENT_GAP // 在下一个引用分支前保留清晰留白。
  }
  if (options.includeIsolates && isolateSeeds.length) { // 用户手动展开时在主图下方添加规则网格。
    const columns = Math.max(2, Math.floor((width - HORIZONTAL_MARGIN * 2) / 150)) // 按画布宽度计算孤立论文网格列数。
    const gridStartY = Math.max(cursorY, TOP_MARGIN + 120) // 避免孤立网格与最后一个引用分支重叠。
    isolateSeeds.sort(compareSeed).forEach((seed, index) => { // 使用年份和标题稳定排列孤立论文。
      const column = index % columns // 计算当前论文所在网格列。
      const row = Math.floor(index / columns) // 计算当前论文所在网格行。
      positionedNodes.push({ ...seed, x: HORIZONTAL_MARGIN + 52 + column * ISOLATE_GRID_GAP, y: gridStartY + 44 + row * 74, radius: 8, inDegree: 0, outDegree: 0, community: -1, isIsolate: true, showLabel: false, labelSide: column < columns / 2 ? 'left' : 'right' }) // 写入不参与引用社区颜色的孤立论文节点。
    })
    cursorY = gridStartY + Math.ceil(isolateSeeds.length / columns) * 74 + 74 // 扩展画布高度以完整容纳孤立网格。
  }
  const labelIds = new Set([...positionedNodes].filter((node) => !node.isIsolate).sort((left, right) => (right.inDegree + right.outDegree) - (left.inDegree + left.outDegree) || left.title.localeCompare(right.title, 'en')).slice(0, Math.min(10, mainSeeds.length)).map((node) => node.id)) // 仅为最重要的少量节点永久显示标题。
  for (const node of positionedNodes) { // 将标签预算和外侧方向写回节点，其他标题仅在悬浮和侧栏显示。
    node.showLabel = labelIds.has(node.id) // 仅为重要节点永久展示标题。
    node.labelSide = minYear !== null && maxYear !== null && minYear !== maxYear ? (node.year === minYear ? 'left' : node.year === maxYear ? 'right' : node.x <= width / 2 ? 'left' : 'right') : (node.x <= width / 2 ? 'left' : 'right') // 首末年份固定外置，其余年份也向各自外侧展开。
  }
  const nodeById = new Map(positionedNodes.map((node) => [node.id, node])) // 建立坐标索引供边路径生成。
  const sameYearLaneByEdgeId = new Map<string, number>() // 保存同年份真实引用边的稳定独立轨道。
  const sameYearGroups = new Map<string, VisualEdge[]>() // 按年份列和内部方向分组，避免不同列共享轨道编号。
  for (const edge of visibleEdges.filter((item) => item.edgeType === 'cites')) { // 只为带箭头的真实引用边分配同年轨道。
    const source = nodeById.get(edge.sourceId) // 读取已定位的引用方节点。
    const target = nodeById.get(edge.targetId) // 读取已定位的被引方节点。
    if (!source || !target || source.year !== target.year || source.x !== target.x) continue // 跨年份边直接使用内部平缓曲线，无需同年轨道。
    const groupKey = `${source.year ?? 'unknown'}:${source.labelSide}` // 在同一时间列的同一内部侧共享轨道队列。
    const group = sameYearGroups.get(groupKey) || [] // 读取现有同年边组。
    group.push(edge) // 加入当前真实引用边。
    sameYearGroups.set(groupKey, group) // 写回轨道分组。
  }
  for (const group of sameYearGroups.values()) { // 为每组同年边分配稳定、互不重叠的轨道序号。
    group.sort((left, right) => `${left.sourceId}:${left.targetId}`.localeCompare(`${right.sourceId}:${right.targetId}`, 'en')).forEach((edge, index) => sameYearLaneByEdgeId.set(`${edge.edgeType}:${edge.sourceId}:${edge.targetId}`, index)) // 按标识排序避免渲染刷新时轨道跳变。
  }
  const layoutEdges = visibleEdges.map((edge) => { // 将当前可见事实关系投影为可绘制路径。
    const source = nodeById.get(edge.sourceId) // 读取边起点的已定位节点。
    const target = nodeById.get(edge.targetId) // 读取边终点的已定位节点。
    if (!source || !target) return null // 孤立节点折叠后不应保留指向隐藏节点的边。
    const edgeId = `${edge.edgeType}:${edge.sourceId}:${edge.targetId}` // 构造与轨道映射一致的稳定边标识。
    return { id: edgeId, sourceId: edge.sourceId, targetId: edge.targetId, edgeType: edge.edgeType, path: buildEdgePath(source, target, edge.edgeType, sameYearLaneByEdgeId.get(edgeId) || 0) } // 保持事实方向并生成避开标题、圆形和箭头的路径。
  }).filter((edge): edge is CitationLayoutEdge => edge !== null) // 去除被孤立折叠或邻域过滤隐藏的边。
  return { width, height: Math.max(300, cursorY + 28), nodes: displayedSeeds.length ? positionedNodes : [], edges: layoutEdges, isolatedCount: isolateSeeds.length, componentCount: sortedComponents.length, yearTicks } // 返回供 D3 组件直接渲染的完整布局。
}
