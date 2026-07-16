import { onScopeDispose, ref } from 'vue' // 管理比较选择、请求状态和组件卸载后的过期响应。

/**
 * 统一管理二至五篇已保存论文的事实型比较选择与读取流程。
 *
 * @param {{ comparePapers: (paperIds: string[]) => Promise<object>, getErrorMessage?: (error: unknown) => string, minPapers?: number, maxPapers?: number }} options 比较读取依赖、提示转换和数量边界。
 * @returns {object} 可供搜索页和文献库页直接绑定的比较状态与动作。
 */
export function usePaperComparison({ comparePapers, getErrorMessage = () => '读取论文比较结果时出现未知错误，请稍后重试', minPapers = 2, maxPapers = 5 }) { // 仅接收来源无关的比较请求函数，避免依赖页面数据集合。
  const comparisonPaperIds = ref([]) // 按用户点击顺序保存当前选择的稳定论文标识。
  const comparisonResult = ref(null) // 保存后端返回的事实型固定列结果。
  const comparisonLoading = ref(false) // 标记比较请求是否正在读取已保存快照。
  const comparisonError = ref('') // 保存安全且可展示的选择或请求错误。
  let requestVersion = 0 // 使选择变化、关闭和卸载后的迟到请求失效。
  function isPaperSelectionDisabled(paper) { // 在达到上限时仅禁用未选论文。
    const paperId = normalizePaperId(paper) // 支持论文对象和字符串标识两种页面调用。
    return comparisonPaperIds.value.length >= maxPapers && !comparisonPaperIds.value.includes(paperId) // 已选论文始终允许取消。
  }

  function isPaperSelected(paper) { // 返回论文是否已被加入当前比较集合。
    return comparisonPaperIds.value.includes(normalizePaperId(paper)) // 复用同一个标识规范化规则。
  }

  function invalidateComparison() { // 在选择变化或关闭时使旧结果和在途请求不再可信。
    requestVersion += 1 // 阻止迟到响应覆盖新的选择状态。
    comparisonResult.value = null // 旧结果对应的论文集合已经失效。
    comparisonError.value = '' // 选择变化后清除旧错误提示。
  }

  function togglePaperComparison(paper) { // 将一篇论文加入或移出保持顺序的比较集合。
    const paperId = normalizePaperId(paper) // 不信任页面传入对象的其他字段。
    if (!paperId) { // 缺少标识时无法安全加入比较。
      comparisonError.value = '无法比较论文：缺少论文标识' // 返回可操作但不泄露实现细节的提示。
      return // 不改变现有选择。
    }
    const index = comparisonPaperIds.value.indexOf(paperId) // 查找当前论文是否已被选择。
    if (index >= 0) { // 已选择论文再次点击时取消选择。
      comparisonPaperIds.value.splice(index, 1) // 原地删除以保留其余论文的用户选择顺序。
      invalidateComparison() // 选择变化后旧比较结果必须失效。
      return // 不继续执行新增上限判断。
    }
    if (comparisonPaperIds.value.length >= maxPapers) { // 第六篇论文不得进入前端选择集合。
      comparisonError.value = `一次最多比较 ${maxPapers} 篇论文` // 保持搜索页和文献库原有选择上限提示。
      return // 不发起请求也不修改现有选择。
    }
    comparisonPaperIds.value.push(paperId) // 按用户点击顺序追加稳定论文标识。
    invalidateComparison() // 新选择使旧事实型结果失效。
  }

  function removePaperComparison(paper) { // 支持文献库删除收藏后同步移除对应论文选择。
    const paperId = normalizePaperId(paper) // 接受删除记录中的论文对象或 paper_id。
    const index = comparisonPaperIds.value.indexOf(paperId) // 查找需要移除的比较列。
    if (index < 0) return // 不在选择集合中时无需改变比较状态。
    comparisonPaperIds.value.splice(index, 1) // 保持其他已选论文的顺序。
    invalidateComparison() // 结果不能继续引用已删除论文。
  }

  function clearPaperComparison() { // 清空选择、结果和错误，恢复初始比较状态。
    requestVersion += 1 // 使清空前的异步响应失效。
    comparisonPaperIds.value = [] // 移除所有用户选择。
    comparisonResult.value = null // 关闭或清空比较结果。
    comparisonError.value = '' // 清除上次选择或请求错误。
    comparisonLoading.value = false // 防御清空时遗留加载状态。
  }

  async function openPaperComparison() { // 按当前用户选择顺序读取后端已保存论文事实。
    if (comparisonPaperIds.value.length < minPapers) { // 少于最小数量时没有比较意义。
      comparisonError.value = `请至少选择 ${minPapers} 篇论文进行比较` // 不发送无效比较请求。
      return // 保持当前选择供用户继续补充。
    }
    const version = ++requestVersion // 固定本次请求对应的选择版本。
    const paperIds = [...comparisonPaperIds.value] // 复制当前顺序，防止请求期间数组变化。
    comparisonLoading.value = true // 显示事实型比较读取状态。
    comparisonError.value = '' // 清除上次请求失败提示。
    try { // 调用页面注入的既有比较 API。
      const result = await comparePapers(paperIds) // 后端继续负责搜索快照与文献库回退边界。
      if (version !== requestVersion) return // 选择已变化、关闭或卸载时忽略迟到结果。
      comparisonResult.value = result // 仅展示与当前选择版本一致的事实型结果。
    } catch (error) { // 保持现有 API 领域错误到安全提示的映射。
      if (version !== requestVersion) return // 旧请求不能覆盖新选择的错误状态。
      comparisonError.value = getErrorMessage(error) // 由页面提供既有领域错误判断。
    } finally { // 恢复工具栏与弹窗操作。
      if (version === requestVersion) comparisonLoading.value = false // 仅结束当前请求自己的加载状态。
    }
  }

  function closePaperComparison() { // 关闭弹窗但保留用户选择，便于继续调整后再次比较。
    requestVersion += 1 // 关闭后不允许迟到请求重新打开结果。
    comparisonResult.value = null // 仅关闭结果展示。
    comparisonError.value = '' // 清除弹窗内错误提示。
    comparisonLoading.value = false // 防御关闭时遗留加载状态。
  }

  onScopeDispose(closePaperComparison) // 页面卸载后丢弃未完成请求并释放结果状态。
  return { comparisonPaperIds, comparisonResult, comparisonLoading, comparisonError, togglePaperComparison, openPaperComparison, clearPaperComparison, closePaperComparison, removePaperComparison, isPaperSelected, isPaperSelectionDisabled } // 暴露页面组合所需的完整公共交互。
}

function normalizePaperId(paper) { // 统一读取论文对象或字符串中的稳定标识。
  return typeof paper === 'string' ? paper.trim() : String(paper?.paper_id || '').trim() // 空值稳定转为空字符串供调用方防御处理。
}
