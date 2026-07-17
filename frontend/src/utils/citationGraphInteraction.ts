/** 管理引用图选择、邻域焦点与渲染边过滤的纯前端交互规则。 */

/** 描述引用图三类彼此独立的交互状态。 */
export interface CitationGraphInteractionState {
  selectedNodeId: string | null // 保存用户持久点击选择的视觉节点标识。
  focusedPaperId: string | null // 保存仅由“一阶邻域”按钮设置的固定论文焦点。
  hoveredNodeId: string | null // 保存仅用于临时视觉反馈的悬浮视觉节点标识。
}

/** 描述可按端点筛选的最小图边契约。 */
export interface RelationshipEdge {
  sourceId: string // 保存关系起点的视觉节点标识。
  targetId: string // 保存关系终点的视觉节点标识。
}

/** 仅更新持久选中节点，绝不改变邻域焦点或悬浮状态。 */
export function selectCitationGraphNode(state: CitationGraphInteractionState, nodeId: string): CitationGraphInteractionState {
  return { ...state, selectedNodeId: nodeId } // 普通点击只切换关系高亮中心和侧栏论文。
}

/** 仅在用户明确请求一阶邻域时更新固定邻域焦点。 */
export function focusCitationGraphPaper(state: CitationGraphInteractionState, paperId: string): CitationGraphInteractionState {
  return { ...state, focusedPaperId: paperId } // 保留当前选中节点，使侧栏继续展示同一篇论文。
}

/** 返回全局网络时只清空邻域焦点，保留当前选中节点。 */
export function resetCitationGraphFocus(state: CitationGraphInteractionState): CitationGraphInteractionState {
  return { ...state, focusedPaperId: null } // 让布局恢复全局节点集合而不丢失关系选择。
}

/** 清除持久选择和临时悬浮，但不擅自退出一阶邻域。 */
export function clearCitationGraphSelection(state: CitationGraphInteractionState): CitationGraphInteractionState {
  return { ...state, selectedNodeId: null, hoveredNodeId: null } // 恢复当前布局范围内全部边的淡化轮廓。
}

/** 以悬浮优先、点击兜底的方式确定当前关系展示中心。 */
export function resolveRelationshipNodeId(hoveredNodeId: string | null, selectedNodeId: string | null): string | null {
  return hoveredNodeId || selectedNodeId // 悬浮结束后自然回退到持久选择，不修改任何状态。
}

/** 仅保留与当前关系中心直接相连的边；无中心时保留当前布局的全部边。 */
export function filterRelationshipEdges<T extends RelationshipEdge>(edges: readonly T[], relationshipNodeId: string | null): T[] {
  if (!relationshipNodeId) return [...edges] // 初始状态需要渲染完整网络轮廓。
  return edges.filter((edge) => edge.sourceId === relationshipNodeId || edge.targetId === relationshipNodeId) // 选中或悬浮后让无关边完全不进入 SVG 数据绑定。
}
