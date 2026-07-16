import { onScopeDispose, ref } from 'vue' // 管理详情抽屉的独立响应式状态与组件卸载清理。

/**
 * 统一管理已保存论文详情的打开、关闭和过期请求防护。
 *
 * @param {{ getPaperDetail: (paperId: string) => Promise<object>, getErrorMessage?: (error: unknown) => string }} options 详情读取依赖和安全错误转换函数。
 * @returns {{ detailPaper: import('vue').Ref<object | null>, detailLoading: import('vue').Ref<boolean>, detailError: import('vue').Ref<string>, openPaperDetail: (paper: object | string) => Promise<void>, closePaperDetail: () => void, disposePaperDetail: () => void }} 可供页面直接绑定详情抽屉的状态与动作。
 */
export function usePaperDetail({ getPaperDetail, getErrorMessage = () => '读取论文详情时出现未知错误，请稍后重试' }) { // 仅接收来源无关的详情读取函数，避免依赖任一页面状态。
  const detailPaper = ref(null) // 保存当前已确认属于最新请求的论文详情。
  const detailLoading = ref(false) // 标记当前详情请求是否仍在进行。
  const detailError = ref('') // 保存可安全展示的详情错误。
  let requestVersion = 0 // 递增版本号，使快速切换或卸载后的迟到响应失效。

  async function openPaperDetail(paper) { // 支持接收论文对象或稳定 paper_id 字符串。
    const paperId = typeof paper === 'string' ? paper.trim() : String(paper?.paper_id || '').trim() // 统一读取并规范化详情请求标识。
    const version = ++requestVersion // 在新请求开始前使旧请求永久失效。
    detailPaper.value = null // 请求前清除旧论文，避免加载期误显示上一条详情。
    detailError.value = '' // 请求前清除旧错误，避免用户误判当前论文失败。
    if (!paperId) { // 无有效标识时不得发出不可信请求。
      detailError.value = '无法读取论文详情：缺少论文标识' // 返回不泄露实现细节的安全提示。
      return // 保持非加载状态。
    }
    detailLoading.value = true // 仅在有效请求时显示抽屉加载状态。
    try { // 详情来源由调用方注入，Composable 不直接耦合具体 API 模块。
      const detail = await getPaperDetail(paperId) // 读取后端已保存快照中的论文详情。
      if (version !== requestVersion) return // 快速切换或卸载后忽略不再属于当前论文的成功响应。
      detailPaper.value = detail // 仅写入最新请求对应的详情。
    } catch (error) { // 网络、后端与契约错误都转换为页面可展示文案。
      if (version !== requestVersion) return // 旧请求失败同样不能覆盖当前详情状态。
      detailError.value = getErrorMessage(error) // 由页面保留领域错误类型和既有提示文本。
    } finally { // 结束当前请求的加载状态。
      if (version === requestVersion) detailLoading.value = false // 仅允许最新请求结束其自己的加载状态。
    }
  }

  function closePaperDetail() { // 关闭抽屉并让任何在途请求不能再写入状态。
    requestVersion += 1 // 使关闭前发起的迟到响应失效。
    detailPaper.value = null // 释放当前详情内容。
    detailError.value = '' // 关闭后不保留旧错误。
    detailLoading.value = false // 防御关闭时遗留加载展示。
  }

  function disposePaperDetail() { // 在页面卸载时复用关闭语义，避免迟到响应写入失效组件。
    closePaperDetail() // 不需要直接操作 DOM 或页面特有状态。
  }

  onScopeDispose(disposePaperDetail) // 组件卸载后自动清理本 Composable 的异步状态。
  return { detailPaper, detailLoading, detailError, openPaperDetail, closePaperDetail, disposePaperDetail } // 暴露稳定且页面无关的详情边界。
}
