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
  labelText: string // 保存经语义缩放后的短标题文本。
  labelBox: LayoutRect // 保存经候选代价选择后的实际标签占位矩形。
}

export interface CitationLayoutEdge { // 描述可直接绘制为 SVG 路径的关系边。
  id: string // 保存关系的稳定去重键。
  sourceId: string // 保存视觉起点节点标识。
  targetId: string // 保存视觉终点节点标识。
  edgeType: CitationEdgeType // 保持引用与版本族的视觉边界。
  path: string // 保存避开节点、标签和年份标题的圆角分段路径。
  points: LayoutPoint[] // 保存用于测试和箭头末端切线的实际路由点。
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
    const componentEdges = visibleEdges.filter((edge) => memberIds.has(edge.sourceId) && memberIds.has(edge.targetId)) // 只将当前分支的事实边用于同年排序。
    minimizeLayerCrossings(buckets, componentEdges) // 按相邻年份节点重心稳定重排，尽量减少跨年边交叉。
    const largestBucket = Math.max(...[...buckets.values()].map((bucket) => bucket.length), 1) // 根据同年最大节点数预留分支高度。
    const componentHeight = Math.max(132, largestBucket * NODE_ROW_GAP + 44) // 保证小分支和同年密集分支均有可读空间。
    for (const [year, bucket] of buckets) { // 为分支内每个年份列分配稳定纵向行。
      bucket.sort(compareSeed).forEach((seed, index) => { // 在同年中按标题和标识稳定排列。
        const nodeInDegree = inDegree.get(seed.id) || 0 // 读取当前节点真实被引数量。
        const nodeOutDegree = outDegree.get(seed.id) || 0 // 读取当前节点真实引用数量。
        const radius = Math.min(22, 8 + Math.log1p(nodeInDegree) * 5) // 使用入度对数缩放，避免单节点过大。
        positionedNodes.push({ ...seed, x: xForYear(year), y: cursorY + 28 + index * NODE_ROW_GAP, radius, inDegree: nodeInDegree, outDegree: nodeOutDegree, community: component, isIsolate: false, showLabel: false, labelText: shortenCitationLabel(seed.title), labelBox: { x: 0, y: 0, width: 0, height: 0 } }) // 写入主图节点的完整稳定渲染数据。
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
      positionedNodes.push({ ...seed, x: 52 + column * ISOLATE_GRID_GAP, y: gridStartY + 44 + row * 74, radius: 8, inDegree: 0, outDegree: 0, community: -1, isIsolate: true, showLabel: false, labelText: shortenCitationLabel(seed.title), labelBox: { x: 0, y: 0, width: 0, height: 0 } }) // 写入不参与引用社区颜色的孤立论文节点。
    })
    cursorY = gridStartY + Math.ceil(isolateSeeds.length / columns) * 74 + 74 // 扩展画布高度以完整容纳孤立网格。
  }
  const labelBudget = width < 760 ? 5 : 10 // 窄画布按语义缩放减少默认常驻标题数量。
  const importantLabelIds = new Set([...positionedNodes].filter((node) => !node.isIsolate).sort((left, right) => (right.inDegree + right.outDegree) - (left.inDegree + left.outDegree) || left.title.localeCompare(right.title, 'en')).slice(0, Math.min(labelBudget, mainSeeds.length)).map((node) => node.id)) // 在可用空间内只保留最重要论文的默认标签。
  for (const node of positionedNodes) node.showLabel = importantLabelIds.has(node.id) // 其余论文仅在悬浮、选中或详情中显示标题。
  const nodeById = new Map(positionedNodes.map((node) => [node.id, node])) // 建立坐标索引供边路径生成。
  const height = Math.max(300, cursorY + 28) // 在标签和路由前固定当前画布有效高度。
  const canvas = { x: 0, y: 0, width, height } // 将画布边界作为标签和边路由的障碍约束。
  const yearTitleBoxes = [...yearColumns.entries()].filter(([year]) => year !== null).map(([year, x]) => ({ x: x - String(year).length * 3.5 - 5, y: 14, width: String(year).length * 7 + 10, height: 18 })) // 让标签和边避开年份轴标题文本。
  const provisionalSegments: Array<[LayoutPoint, LayoutPoint]> = [] // 用简化正交预览让标签选择提前考虑边位置。
  for (const edge of visibleEdges) { // 为每条可见事实边建立不依赖标签的初始折线。
    const source = nodeById.get(edge.sourceId) // 读取源节点。
    const target = nodeById.get(edge.targetId) // 读取目标节点。
    if (!source || !target) continue // 隐藏节点不进入标签代价。
    provisionalSegments.push([{ x: source.x, y: source.y }, { x: target.x, y: source.y }], [{ x: target.x, y: source.y }, { x: target.x, y: target.y }]) // 使用两段正交预览估算标签与边的相交。
  }
  const labelMetrics = measureLabelBoxes(positionedNodes) // 先测量实际短标题的宽高，避免仅在渲染阶段才处理文字。
  const allLabelBoxes = chooseLabelPositions(positionedNodes, labelMetrics, provisionalSegments, canvas, yearTitleBoxes) // 在八向候选中最小化标签、节点、边和年份标题冲突。
  for (const node of positionedNodes) node.labelBox = allLabelBoxes.get(node.id) || { x: node.x, y: node.y, width: 0, height: 0 } // 将纯函数选择的标签矩形写回渲染节点。
  const visibleLabelBoxes = new Map(positionedNodes.filter((node) => node.showLabel).map((node) => [node.id, node.labelBox])) // 默认只让语义缩放后可见的标签参与边避让。
  const portAssignments = assignNodePorts(positionedNodes, visibleEdges, visibleLabelBoxes) // 根据端口占用、路径长度和可见标签动态选择两端连接点。
  const edgeLanes = allocateEdgeLanes(positionedNodes, visibleEdges, portAssignments, yearColumns, visibleLabelBoxes, canvas, yearTitleBoxes) // 在年份间和同年外部通道内分配无碰撞优先的车道。
  const layoutEdges = routeEdgesAroundObstacles(positionedNodes, visibleEdges, portAssignments, edgeLanes) // 将端口和车道组合为圆角分段避障路径。
  return { width, height, nodes: displayedSeeds.length ? positionedNodes : [], edges: layoutEdges, isolatedCount: isolateSeeds.length, componentCount: sortedComponents.length, yearTicks } // 返回供 D3 组件直接渲染的完整布局。
}
