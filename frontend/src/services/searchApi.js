/** 表示多源搜索请求或响应无法完成的公共前端错误。 */
export class SearchApiError extends Error { // 继承标准错误以便页面统一捕获。
  constructor(message, status = null) { // 接收可展示消息和可选 HTTP 状态码。
    super(message) // 保存错误消息供界面展示。
    this.name = 'SearchApiError' // 提供稳定错误类型名称便于调试。
    this.status = status // 保存状态码但不暴露后端内部响应。
  }
}

const DEFAULT_API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL || '').replace(/\/$/, '') // 默认依赖 Vite 代理，也允许部署时指定 API 根地址。

/** 将逗号或换行分隔的用户条件转换为去重后的字符串列表。 */
export function splitTerms(value) { // 接收表单中的自由文本条件。
  const seen = new Set() // 记录大小写无关的已出现词项。
  return String(value || '') // 将空值安全转换为空字符串。
    .split(/[,，\n]/) // 同时支持中英文逗号与换行。
    .map((term) => term.trim()) // 清除每个词项首尾空白。
    .filter((term) => { // 移除空值和重复条件。
      const key = term.toLocaleLowerCase() // 使用大小写无关键比较英文条件。
      if (!term || seen.has(key)) return false // 跳过无效或重复词项。
      seen.add(key) // 标记当前词项已接受。
      return true // 保留首次出现的有效条件。
    })
}

/** 根据查询字符粗略判断后端 QueryIntent 所需语言枚举。 */
export function detectQueryLanguage(queryText) { // 接收用户原始查询。
  const hasChinese = /[\u3400-\u9fff]/u.test(queryText) // 判断是否包含中日韩统一表意字符。
  const hasLatin = /[a-z]/iu.test(queryText) // 判断是否包含拉丁字母。
  if (hasChinese && hasLatin) return 'mixed' // 同时存在两类字符时标记中英混合。
  return hasChinese ? 'zh' : 'en' // 其余情况按中文或英文返回稳定枚举。
}

/** 将搜索页表单转换为后端稳定 QueryIntent 契约。 */
export function createQueryIntent(form) { // 接收页面维护的响应式表单快照。
  const queryText = String(form.queryText || '').trim() // 规范化必填自然语言查询。
  if (!queryText) throw new SearchApiError('请输入需要检索的研究问题') // 在发出请求前给出明确校验消息。
  const startYear = form.startYear ? Number(form.startYear) : null // 将可选起始年份转换为数字。
  const endYear = form.endYear ? Number(form.endYear) : null // 将可选结束年份转换为数字。
  if ((startYear && !endYear) || (!startYear && endYear)) throw new SearchApiError('年份范围需要同时填写起始和结束年份') // 防止提交语义不完整的年份约束。
  if (startYear && endYear && startYear > endYear) throw new SearchApiError('起始年份不能晚于结束年份') // 与后端年份校验保持一致。
  const mustInclude = splitTerms(form.mustInclude) // 提前规范化必须词用于冲突校验和请求映射。
  const shouldInclude = splitTerms(form.shouldInclude) // 提前规范化软偏好用于冲突校验和请求映射。
  const exclude = splitTerms(form.exclude) // 提前规范化排除词用于冲突校验和请求映射。
  const excludedKeys = new Set(exclude.map((term) => term.toLocaleLowerCase())) // 构造大小写无关的排除词集合。
  if ([...mustInclude, ...shouldInclude].some((term) => excludedKeys.has(term.toLocaleLowerCase()))) throw new SearchApiError('必须或优先条件不能同时出现在排除条件中') // 在请求前阻止相互矛盾的约束。
  return { // 返回可直接序列化的 QueryIntent。
    original_query: queryText, // 保留用户原始查询供界面回显。
    normalized_query: queryText.replace(/\s+/g, ' '), // 合并连续空白形成稳定检索文本。
    query_language: detectQueryLanguage(queryText), // 提供跨语言排序所需语言标识。
    research_topics: [queryText], // 在 Query Agent 接入前将完整研究问题作为主题召回入口。
    must_include: mustInclude, // 映射必须满足条件。
    should_include: shouldInclude, // 映射优先满足条件。
    exclude, // 映射排除条件。
    year_range: startYear && endYear ? [startYear, endYear] : null, // 仅在完整填写时提交闭区间。
    target_paper_count: 20, // 与当前 LLM 最终结果上限保持一致。
    search_mode: 'standard', // 搜索页统一固定为最多两轮的标准检索策略。
    enable_semantic_ranking: Boolean(form.enableSemanticRanking), // 保存用户对 BGE-M3 粗排的显式选择。
    enable_cross_encoder_ranking: Boolean(form.enableCrossEncoderRanking), // 保存用户对 Cross Encoder 重排的显式选择。
    domains: splitTerms(form.domains), // 提供动态 arXiv 与 DBLP 路由依据。
    requires_web_evidence: Boolean(form.requiresWebEvidence), // 仅在用户明确选择时启用网页补充发现。
  }
}

/** 将表单转换为后端自然语言查询规划入口请求。 */
export function createNaturalSearchRequest(form) { // 复用已有表单校验但不再由前端伪造 QueryIntent。
  const intent = createQueryIntent(form) // 校验查询、年份和条件冲突。
  return { // 只提交用户真正输入的内容，由后端 Query Agent 提取语义字段。
    query: intent.original_query, // 保存原始自然语言问题。
    search_mode: intent.search_mode, // 保存搜索模式。
    enable_semantic_ranking: intent.enable_semantic_ranking, // 将 BGE-M3 选择交给 Query Agent 后续透传。
    enable_cross_encoder_ranking: intent.enable_cross_encoder_ranking, // 将 Cross Encoder 选择交给 Query Agent 后续透传。
    year_range: intent.year_range, // 保存显式年份覆盖。
    must_include: intent.must_include, // 保存显式必须条件。
    should_include: intent.should_include, // 保存显式偏好条件。
    exclude: intent.exclude, // 保存显式排除条件。
    domains: intent.domains, // 保存用户显式领域提示。
    requires_web_evidence: intent.requires_web_evidence, // 保存网页证据开关。
    target_paper_count: 20, // 保持最终最多二十篇。
  }
}

/** 调用多轮自然语言检索接口并返回结构化结果。 */
export async function searchPapers(form, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许测试注入离线 fetch 替身。
  const naturalRequest = createNaturalSearchRequest(form) // 在网络调用前生成自然语言规划请求。
  return postSearch('/api/v1/search/natural-multi-round', naturalRequest, fetchImpl, apiBaseUrl) // 调用先规划再执行有限轮次检索的自然语言入口。
}

/** 使用 SSE 流执行自然语言多轮检索，并在完成后读取同次运行的最终结果。 */
export async function streamSearchPapers(form, onEvent, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许页面消费进度且测试注入离线 fetch。
  const naturalRequest = createNaturalSearchRequest(form) // 在连接 SSE 前复用自然语言请求校验。
  return streamSearch('/api/v1/search/natural-multi-round/events', naturalRequest, onEvent, fetchImpl, apiBaseUrl) // 使用不重复检索的自然语言事件流入口。
}

/** 校验并复制用户编辑后的 QueryIntent，避免直接修改上一轮响应。 */
export function validateQueryIntent(intent) { // 接收查询解析面板提交的完整意图。
  if (!intent || typeof intent !== 'object') throw new SearchApiError('查询解析结果不完整') // 拒绝空对象或错误类型。
  const nextIntent = structuredCloneSafe(intent) // 创建独立副本以保护上一轮结果。
  nextIntent.search_mode = 'standard' // 编辑历史深度运行时也统一回到两轮标准检索。
  nextIntent.original_query = String(nextIntent.original_query || '').trim() // 保留原始问题作为检索审计上下文。
  nextIntent.normalized_query = String(nextIntent.normalized_query || '').trim().replace(/\s+/g, ' ') // 规范化英文检索式空白。
  if (!nextIntent.original_query || !nextIntent.normalized_query) throw new SearchApiError('原始问题和英文检索式不能为空') // 防止提交无法审计或无法召回的计划。
  const targetCount = Number(nextIntent.target_paper_count || 20) // 读取最终结果上限。
  const sourceCount = Number(nextIntent.source_recall_count || targetCount) // 读取每来源召回上限。
  if (!Number.isInteger(targetCount) || targetCount < 1 || targetCount > 20) throw new SearchApiError('最终结果数量必须在 1 到 20 之间') // 与后端产品上限保持一致。
  if (!Number.isInteger(sourceCount) || sourceCount < targetCount || sourceCount > 100) throw new SearchApiError('来源召回数量必须不少于最终结果且不超过 100') // 保证候选充足且符合后端成本上限。
  nextIntent.target_paper_count = targetCount // 写回已校验整数。
  nextIntent.source_recall_count = sourceCount // 写回已校验整数。
  if (nextIntent.year_range !== null && nextIntent.year_range !== undefined) { // 仅在用户设置年份时校验闭区间。
    if (!Array.isArray(nextIntent.year_range) || nextIntent.year_range.length !== 2) throw new SearchApiError('年份范围需要同时填写起始和结束年份') // 拒绝不完整区间。
    const [startYear, endYear] = nextIntent.year_range.map(Number) // 转换编辑框文本为数字。
    if (!Number.isInteger(startYear) || !Number.isInteger(endYear) || startYear > endYear) throw new SearchApiError('起始年份不能晚于结束年份') // 拒绝非法或倒置年份。
    nextIntent.year_range = [startYear, endYear] // 写回稳定数字闭区间。
  }
  const mustInclude = Array.isArray(nextIntent.must_include) ? nextIntent.must_include : [] // 读取必须条件。
  const shouldInclude = Array.isArray(nextIntent.should_include) ? nextIntent.should_include : [] // 读取偏好条件。
  const exclude = Array.isArray(nextIntent.exclude) ? nextIntent.exclude : [] // 读取排除条件。
  const paperTypes = Array.isArray(nextIntent.paper_types) ? nextIntent.paper_types : [] // 读取论文类型筛选条件。
  const allowedPaperTypes = new Set(['article', 'conference', 'preprint', 'review']) // 与后端稳定枚举保持一致。
  if (paperTypes.some((paperType) => !allowedPaperTypes.has(String(paperType)))) throw new SearchApiError('论文类型仅支持 article、conference、preprint 或 review') // 在请求前给出可编辑错误。
  const excludedKeys = new Set(exclude.map((term) => String(term).trim().toLocaleLowerCase())) // 建立大小写无关排除集合。
  if ([...mustInclude, ...shouldInclude].some((term) => excludedKeys.has(String(term).trim().toLocaleLowerCase()))) throw new SearchApiError('必须或优先条件不能同时出现在排除条件中') // 阻止逻辑冲突。
  return nextIntent // 返回可直接发送到后端的独立完整意图。
}

/** 使用已编辑 QueryIntent 直接重搜，从而跳过 Query Agent 调用。 */
export async function searchWithIntent(intent, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许测试注入离线 fetch。
  const validatedIntent = validateQueryIntent(intent) // 在网络调用前验证完整编辑结果。
  return postSearch('/api/v1/search/multi-round', validatedIntent, fetchImpl, apiBaseUrl) // 直接进入多轮检索并保持跳过 Query Agent。
}

/** 使用 SSE 流直接执行已编辑 QueryIntent，并在完成后读取同次运行的最终结果。 */
export async function streamSearchWithIntent(intent, onEvent, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 保持编辑重搜跳过 Query Agent 且向页面反馈进度。
  const validatedIntent = validateQueryIntent(intent) // 在建立流前先验证用户编辑后的完整意图。
  return streamSearch('/api/v1/search/multi-round/events', validatedIntent, onEvent, fetchImpl, apiBaseUrl) // 使用直接意图多轮事件流入口。
}

/** 提交统一搜索请求并校验 MultiRoundSearchResult 最小契约。 */
async function postSearch(path, requestBody, fetchImpl, apiBaseUrl) { // 复用自然入口和直接意图入口的错误边界。
  if (typeof fetchImpl !== 'function') throw new SearchApiError('当前环境不支持网络请求') // 在旧环境给出可理解错误。
  let response // 保存 HTTP 响应供后续状态处理。
  try { // 将浏览器网络错误转换为稳定前端错误。
    response = await fetchImpl(`${apiBaseUrl}${path}`, { // 调用指定的稳定搜索入口。
      method: 'POST', // 使用 POST 提交结构化复杂查询。
      headers: { 'Content-Type': 'application/json' }, // 明确发送 UTF-8 JSON 请求。
      body: JSON.stringify(requestBody), // 序列化自然请求或已编辑 QueryIntent。
    })
  } catch { // 捕获断网、代理或后端未启动错误。
    throw new SearchApiError('无法连接检索服务，请确认后端已启动') // 不向页面暴露浏览器底层错误细节。
  }
  if (!response.ok) { // 非成功状态不得被当作搜索结果渲染。
    let message = '论文检索暂时不可用，请稍后重试' // 提供安全默认消息。
    try { // 优先读取 FastAPI 已净化的公共错误说明。
      const errorBody = await response.json() // 解析后端 JSON 错误响应。
      if (typeof errorBody.detail === 'string') message = errorBody.detail // 只接受可展示字符串字段。
    } catch { // 非 JSON 错误响应继续使用默认消息。
      // 无需处理响应正文，避免将代理页面或内部堆栈展示给用户。
    }
    throw new SearchApiError(message, response.status) // 携带状态码供未来埋点但页面只展示安全消息。
  }
  try { // 防止成功状态携带无效 JSON 时页面崩溃。
    const result = await response.json() // 解析后端稳定 MultiRoundSearchResult。
    if (!result || !Array.isArray(result.papers) || typeof result.run_state !== 'object' || typeof result.query_intent !== 'object') throw new SearchApiError('检索服务返回了不完整的结果') // 验证页面渲染依赖的多轮状态和可编辑意图。
    return result // 返回已通过最小契约检查的结果。
  } catch (error) { // 将响应解析失败转换为统一错误。
    if (error instanceof SearchApiError) throw error // 保留本地最小契约检查给出的明确消息。
    throw new SearchApiError('检索服务返回了无法解析的结果') // 提示用户重试且不泄露原始正文。
  }
}

/** 使用 fetch ReadableStream 解析 POST SSE，并在完成事件后读取同次最终结果。 */
async function streamSearch(path, requestBody, onEvent, fetchImpl, apiBaseUrl) { // EventSource 只支持 GET，因此使用可取消的 fetch 流实现 POST 检索。
  if (typeof fetchImpl !== 'function') throw new SearchApiError('当前环境不支持网络请求') // 在旧环境给出可理解错误。
  if (typeof onEvent !== 'function') throw new SearchApiError('检索进度回调不可用') // 防止页面遗漏进度消费函数导致结果无法关联。
  let response // 保存 SSE HTTP 响应供状态和流读取判断。
  try { // 将断网和代理错误转换为稳定前端消息。
    response = await fetchImpl(`${apiBaseUrl}${path}`, { method: 'POST', headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' }, body: JSON.stringify(requestBody) }) // 明确请求 SSE 并提交 JSON 意图。
  } catch { // 捕获无法连接后端等浏览器底层错误。
    throw new SearchApiError('无法连接检索服务，请确认后端已启动') // 不向界面暴露浏览器网络细节。
  }
  if (!response.ok) { // SSE 建连失败时复用稳定公共错误处理。
    throw await parseSearchError(response) // 读取已净化后端 detail 或返回通用错误。
  }
  if (!response.body || typeof response.body.getReader !== 'function') throw new SearchApiError('当前浏览器不支持检索进度流') // 拒绝无法解析流的成功响应。
  const reader = response.body.getReader() // 获取二进制流读取器。
  const decoder = new TextDecoder('utf-8') // 按 UTF-8 还原中文进度消息。
  let buffer = '' // 保存跨网络分块尚未形成完整 SSE 帧的文本。
  let runId = '' // 保存 run_created 事件提供的稳定运行标识。
  try { // 读取直到服务端完成并关闭流。
    while (true) { // 流结束前持续消费任意大小的数据块。
      const { done, value } = await reader.read() // 等待下一块文本或流结束信号。
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done }) // 累积文本并保留不完整 UTF-8 边界。
      const frames = buffer.split('\n\n') // SSE 空行分隔完整事件帧。
      buffer = frames.pop() || '' // 保留最后一个未完整帧等待下一块补全。
      for (const frame of frames) { // 按服务端顺序处理每个完整事件。
        const event = parseSseFrame(frame) // 解析 event 名称和 JSON data 对象。
        if (!event) continue // 跳过心跳、空帧或不完整控制行。
        if (typeof event.run_id === 'string' && event.run_id) runId = event.run_id // 首个创建事件和后续事件均可补充运行标识。
        onEvent(event) // 立即通知页面更新轮次、消息和候选数量。
      }
      if (done) break // 服务端关闭流后退出读取循环。
    }
  } catch (error) { // 统一处理流中断或 JSON 解析异常。
    if (error instanceof SearchApiError) throw error // 保留明确的协议错误。
    throw new SearchApiError('检索进度流意外中断，请稍后重试') // 不向用户暴露底层流异常。
  } finally { // 释放读取器避免页面切换后保留网络资源。
    reader.releaseLock() // 允许浏览器回收响应流锁。
  }
  if (!runId) throw new SearchApiError('检索进度流未返回运行标识') // 没有运行标识时无法安全读取同次最终结果。
  return getSearchRunResult(runId, fetchImpl, apiBaseUrl) // 只读取本次 SSE 已完成运行的结果，不会再次执行检索。
}

/** 按运行标识读取 SQLite 最新轻量状态，用于刷新页面后的恢复入口。 */
export async function getSearchRunState(runId, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许页面和测试复用只读恢复请求。
  const normalizedRunId = String(runId || '').trim() // 规范化 URL 传入的运行标识。
  if (!normalizedRunId) throw new SearchApiError('缺少需要恢复的搜索运行标识') // 防止请求无效资源路径。
  let response // 保存状态读取 HTTP 响应。
  try { // 将浏览器网络失败转换为稳定恢复提示。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/search/runs/${encodeURIComponent(normalizedRunId)}`, { method: 'GET', headers: { Accept: 'application/json' } }) // 只读取已持久化快照，不重新执行搜索。
  } catch { // 捕获断网、代理或服务未启动错误。
    throw new SearchApiError('无法读取已保存的搜索运行，请确认后端已启动') // 不暴露底层网络错误。
  }
  if (!response.ok) throw await parseSearchError(response) // 将不存在或服务故障映射为安全公共错误。
  try { // 校验页面恢复进度所需的最小状态契约。
    const state = await response.json() // 解析 SearchRunState JSON。
    if (!state || typeof state.run_id !== 'string' || typeof state.status !== 'string' || typeof state.query_intent !== 'object') throw new SearchApiError('已保存的搜索运行状态不完整') // 避免页面按无效快照恢复。
    return state // 返回轻量快照，论文集合仍由结果接口独立读取。
  } catch (error) { // 统一处理 JSON 或状态契约异常。
    if (error instanceof SearchApiError) throw error // 保留明确的恢复错误消息。
    throw new SearchApiError('已保存的搜索运行状态无法解析') // 不展示原始响应内容。
  }
}

/** 恢复一次搜索运行：始终读取状态，终态再读取同次最终结果。 */
export async function restoreSearchRun(runId, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 为刷新页面提供不重新检索的组合读取入口。
  const state = await getSearchRunState(runId, fetchImpl, apiBaseUrl) // 先恢复所有运行都具备的轻量状态。
  if (!['completed', 'failed'].includes(state.status)) return { state, result: null } // 运行中或排队状态尚无稳定最终结果，禁止将空集合伪装为完成。
  try { // 仅对终态读取同一 run_id 已持久化的完整结果。
    const result = await getSearchRunResult(state.run_id, fetchImpl, apiBaseUrl) // 不触发任何新的来源调用。
    return { state, result } // 返回终态状态及完整论文集合。
  } catch (error) { // 结果写入与状态快照之间可能存在极短暂延迟。
    if (error instanceof SearchApiError && error.status === 404) return { state, result: null } // 保留已恢复状态并允许页面提示用户稍后刷新。
    throw error // 其余网络或服务故障继续由页面统一展示。
  }
}

/** 读取由 SSE 同次运行持久化的最终结果。 */
export async function getSearchRunResult(runId, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 接收已由服务端生成的稳定运行标识。
  const normalizedRunId = String(runId || '').trim() // 规范化 URL 或 SSE 提供的运行标识。
  if (!normalizedRunId) throw new SearchApiError('缺少需要读取的搜索运行标识') // 防止请求无效结果资源路径。
  let response // 保存最终结果 HTTP 响应。
  try { // 将断网或代理错误转换为统一用户消息。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/search/runs/${encodeURIComponent(normalizedRunId)}/result`, { method: 'GET', headers: { Accept: 'application/json' } }) // 仅读取同次运行的结果快照。
  } catch { // 捕获结果读取阶段网络错误。
    throw new SearchApiError('检索已完成但无法读取最终结果，请稍后重试') // 提示用户可使用 run_id 后续恢复读取。
  }
  if (!response.ok) throw await parseSearchError(response) // 结果尚未就绪或服务故障时返回稳定错误。
  try { // 校验最终结果仍符合页面依赖的多轮响应契约。
    const result = await response.json() // 解析完成结果 JSON。
    if (!result || !Array.isArray(result.papers) || typeof result.run_state !== 'object' || typeof result.query_intent !== 'object') throw new SearchApiError('检索服务返回了不完整的结果') // 防止页面渲染不完整持久化数据。
    return result // 返回同次多轮搜索得到的完整论文结果。
  } catch (error) { // 将 JSON 或契约错误转换为统一提示。
    if (error instanceof SearchApiError) throw error // 保留明确字段缺失错误。
    throw new SearchApiError('检索服务返回了无法解析的结果') // 避免展示原始响应文本。
  }
}

/** 按条件分页读取已保存搜索结果，不重新执行多源检索。 */
export async function getSearchRunPapers(runId, options = {}, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许搜索页和测试复用稳定结果读取请求。
  const normalizedRunId = String(runId || '').trim() // 规范化 SSE、URL 或结果快照提供的运行标识。
  if (!normalizedRunId) throw new SearchApiError('缺少需要读取结果的搜索运行标识') // 阻止无效资源路径进入网络层。
  const page = Number(options.page || 1) // 读取页面当前请求页码。
  const pageSize = Number(options.pageSize || 5) // 读取页面期望的单页数量。
  if (!Number.isInteger(page) || page < 1 || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 20) throw new SearchApiError('结果分页参数无效') // 保持与后端一致的基础分页边界。
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize), sort: String(options.sort || 'relevance') }) // 构建可安全编码的固定查询参数。
  const source = String(options.source || '').trim() // 读取可选来源条件。
  const relevance = String(options.relevance || '').trim() // 读取可选核验状态条件。
  const yearStart = String(options.yearStart || '').trim() // 读取可选年份下界。
  const yearEnd = String(options.yearEnd || '').trim() // 读取可选年份上界。
  if (source && source !== 'all') params.set('source', source) // 仅在用户实际筛选时传递来源字段。
  if (relevance && relevance !== 'all') params.set('relevance', relevance) // 仅在用户实际筛选时传递核验状态。
  if (yearStart) params.set('year_start', yearStart) // 将非空年份下界交给后端统一校验。
  if (yearEnd) params.set('year_end', yearEnd) // 将非空年份上界交给后端统一校验。
  let response // 保存分页读取 HTTP 响应。
  try { // 只发起不改变结果快照的 GET 请求。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/search/runs/${encodeURIComponent(normalizedRunId)}/papers?${params.toString()}`, { method: 'GET', headers: { Accept: 'application/json' } }) // 仅读取同次持久化结果。
  } catch { // 网络故障时不得回退到前端猜测或重新检索。
    throw new SearchApiError('无法读取已保存的搜索结果，请确认后端已启动') // 返回安全且可执行的公共提示。
  }
  if (!response.ok) throw await parseSearchError(response) // 复用后端已净化错误边界。
  try { // 校验结果列表与分页控件所依赖的最小契约。
    const resultPage = await response.json() // 解析服务端筛选、排序后的结果页。
    if (!resultPage || typeof resultPage.run_id !== 'string' || !Array.isArray(resultPage.items) || typeof resultPage.total !== 'number' || typeof resultPage.page !== 'number' || typeof resultPage.page_size !== 'number' || typeof resultPage.total_pages !== 'number') throw new SearchApiError('搜索结果分页数据不完整') // 防止页面以损坏元数据渲染。
    return resultPage // 返回服务端唯一事实源的当前页结果。
  } catch (error) { // 将 JSON 或契约错误转换为统一安全提示。
    if (error instanceof SearchApiError) throw error // 保留可直接展示的业务错误。
    throw new SearchApiError('搜索结果分页数据无法解析') // 不展示原始响应正文。
  }
}

/** 读取包含搜索问题但不含论文内容的本地搜索运行历史。 */
export async function listSearchRuns(limit = 10, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许搜索页和测试复用受控历史读取请求。
  const normalizedLimit = Number(limit) // 规范化调用方提供的历史数量上限。
  if (!Number.isInteger(normalizedLimit) || normalizedLimit < 1 || normalizedLimit > 50) throw new SearchApiError('搜索历史数量上限必须在 1 至 50 之间') // 保持与后端查询参数边界一致。
  let response // 保存历史读取 HTTP 响应。
  try { // 只读取本地 SQLite 索引，不触发来源、模型或完整结果读取。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/search/runs?limit=${normalizedLimit}`, { method: 'GET', headers: { Accept: 'application/json' } }) // 请求固定数量的最近运行元数据。
  } catch { // 网络或服务未启动时不伪造本地历史。
    throw new SearchApiError('无法读取搜索运行历史，请确认后端已启动') // 返回可执行的安全提示。
  }
  if (!response.ok) throw await parseSearchError(response) // 复用后端已净化错误边界。
  try { // 校验历史抽屉所需的最小索引契约。
    const history = await response.json() // 解析包含本地搜索问题的历史响应。
    if (!history || !Array.isArray(history.items) || typeof history.limit !== 'number' || history.items.some((item) => typeof item?.run_id !== 'string' || typeof item.query_text !== 'string' || typeof item.status !== 'string' || typeof item.result_ready !== 'boolean')) throw new SearchApiError('搜索运行历史数据不完整') // 防止页面渲染损坏索引。
    return history // 返回由服务端排序的有限历史列表。
  } catch (error) { // 将 JSON 或契约错误转换为统一安全提示。
    if (error instanceof SearchApiError) throw error // 保留明确业务错误。
    throw new SearchApiError('搜索运行历史数据无法解析') // 不展示原始响应正文。
  }
}

/** 删除用户确认的终态本地搜索运行及同次完整结果快照。 */
export async function deleteSearchRun(runId, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许页面调用受控删除接口而不直接操作 SQLite。
  const normalizedRunId = String(runId || '').trim() // 规范化用户选择的稳定运行标识。
  if (!normalizedRunId) throw new SearchApiError('缺少需要清理的搜索运行标识') // 阻止无效删除资源路径进入网络层。
  let response // 保存删除请求 HTTP 响应。
  try { // 只发送显式用户触发的 DELETE 请求。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/search/runs/${encodeURIComponent(normalizedRunId)}`, { method: 'DELETE', headers: { Accept: 'application/json' } }) // 由后端校验终态并原子清理两类快照。
  } catch { // 网络故障时不在前端假定删除成功。
    throw new SearchApiError('无法清理搜索运行，请确认后端已启动') // 返回可执行的安全提示。
  }
  if (!response.ok) throw await parseSearchError(response) // 将 404、409、503 等边界转为稳定公共错误。
  if (response.status !== 204) throw new SearchApiError('搜索运行清理响应异常', response.status) // 删除接口只接受无正文的成功响应。
}

/** 按论文标识读取 SQLite 已保存详情，不触发新的学术来源检索。 */
export async function getPaperDetail(paperId, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许页面和测试复用只读详情请求。
  const normalizedPaperId = String(paperId || '').trim() // 规范化卡片提供的内部论文标识。
  if (!normalizedPaperId) throw new SearchApiError('缺少需要读取详情的论文标识') // 防止向后端发起无效资源请求。
  let response // 保存详情读取 HTTP 响应。
  try { // 将网络或代理错误转换为可展示的公共提示。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/papers/detail?paper_id=${encodeURIComponent(normalizedPaperId)}`, { method: 'GET', headers: { Accept: 'application/json' } }) // 使用查询参数传递可能包含斜杠的来源论文标识，并仅读取 SQLite 快照。
  } catch { // 不向页面暴露浏览器底层网络异常。
    throw new SearchApiError('无法读取论文详情，请确认后端已启动') // 给出可操作且不泄露实现的信息。
  }
  if (!response.ok) throw await parseSearchError(response) // 复用统一公共错误解析和状态码。
  try { // 校验详情抽屉渲染依赖的最小论文契约。
    const paper = await response.json() // 解析后端返回的 PaperRecord JSON。
    if (!paper || typeof paper.paper_id !== 'string' || typeof paper.title !== 'string' || typeof paper.source !== 'string') throw new SearchApiError('论文详情数据不完整') // 防止页面渲染损坏的历史快照。
    return paper // 返回完整且已校验的详情记录。
  } catch (error) { // 将 JSON 或契约问题转为统一安全提示。
    if (error instanceof SearchApiError) throw error // 保留明确的字段缺失错误。
    throw new SearchApiError('论文详情无法解析') // 避免展示原始响应正文。
  }
}

/** 按用户操作翻译 SQLite 已保存论文的标题与摘要，不向浏览器暴露 DeepSeek 密钥。 */
export async function translatePaperToChinese(paperId, field, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许论文卡片按字段请求中文译文并支持离线测试。
  const normalizedPaperId = String(paperId || '').trim() // 规范化论文卡片提供的稳定内部标识。
  if (!normalizedPaperId) throw new SearchApiError('缺少需要翻译的论文标识') // 阻止无效标识触发后端模型调用。
  if (!['title', 'abstract'].includes(field)) throw new SearchApiError('翻译字段仅支持标题或摘要') // 防止前端请求超出后端允许范围的文本字段。
  let response // 保存翻译请求的 HTTP 响应供统一错误处理。
  try { // 将网络或代理故障转换为页面可展示的公共提示。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/papers/translation/${field}?paper_id=${encodeURIComponent(normalizedPaperId)}`, { method: 'POST', headers: { Accept: 'application/json' } }) // 使用查询参数传递可能包含斜杠的来源论文标识。
  } catch { // 不向用户暴露浏览器底层网络异常。
    throw new SearchApiError('无法翻译论文，请确认后端已启动') // 给出安全且可操作的失败提示。
  }
  if (!response.ok) throw await parseSearchError(response) // 复用统一公共错误解析并保留后端安全摘要。
  try { // 校验卡片渲染中文标题和摘要所需的最小响应契约。
    const translation = await response.json() // 解析后端返回的按需翻译响应。
    if (!translation || translation.field !== field || typeof translation.paper_id !== 'string' || typeof translation.text_zh !== 'string' || typeof translation.model_name !== 'string') throw new SearchApiError('论文翻译数据不完整') // 防止损坏响应覆盖原始论文文本。
    return translation // 返回经过最小校验的中文翻译。
  } catch (error) { // 将 JSON 解析或字段错误映射为稳定前端错误。
    if (error instanceof SearchApiError) throw error // 保留明确的响应契约错误。
    throw new SearchApiError('论文翻译无法解析') // 不展示服务端原始响应正文。
  }
}

/** 比较二至五篇 SQLite 已保存论文，不触发外部来源或 PDF 读取。 */
export async function comparePapers(paperIds, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许页面和测试复用稳定对比请求。
  if (!Array.isArray(paperIds)) throw new SearchApiError('请选择 2 至 5 篇论文进行比较') // 阻止非数组输入进入网络层。
  const normalizedIds = paperIds.map((paperId) => String(paperId || '').trim()) // 规范化卡片提供的内部论文标识。
  if (normalizedIds.length < 2 || normalizedIds.length > 5 || normalizedIds.some((paperId) => !paperId)) throw new SearchApiError('请选择 2 至 5 篇论文进行比较') // 保持前端与后端相同的小集合边界。
  if (new Set(normalizedIds).size !== normalizedIds.length) throw new SearchApiError('比较论文不能重复') // 防止同一论文占据多个固定列。
  let response // 保存论文比较 HTTP 响应。
  try { // 将网络或代理异常转换为安全公共提示。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/compare`, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ paper_ids: normalizedIds }) }) // 仅向后端提交内部标识，不传递或信任前端论文事实。
  } catch { // 不展示浏览器底层网络异常。
    throw new SearchApiError('无法读取论文比较结果，请确认后端已启动') // 给出用户可执行的公共提示。
  }
  if (!response.ok) throw await parseSearchError(response) // 复用统一公共错误解析和状态码。
  try { // 校验固定列对比所需的最小响应契约。
    const comparison = await response.json() // 解析后端事实型对比结果。
    if (!comparison || !Array.isArray(comparison.items) || comparison.items.length !== normalizedIds.length || comparison.items.some((item) => typeof item?.paper_id !== 'string' || typeof item.title !== 'string')) throw new SearchApiError('论文比较结果不完整') // 防止页面渲染不可靠列。
    return comparison // 返回按用户选择顺序排列的详情列。
  } catch (error) { // 将 JSON 或契约错误转换为统一安全提示。
    if (error instanceof SearchApiError) throw error // 保留明确的业务提示。
    throw new SearchApiError('论文比较结果无法解析') // 不展示原始响应正文。
  }
}

/** 读取当前已保存论文集合的受限引用图，不调用外部引文来源。 */
export async function getCitationGraph(paperIds, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL, maxNodes = 30, edgeTypes = []) { // 允许页面和测试复用只读图谱请求。
  if (!Array.isArray(paperIds)) throw new SearchApiError('请选择至少 1 篇论文生成引用图') // 阻止非数组输入进入网络层。
  const normalizedIds = paperIds.map((paperId) => String(paperId || '').trim()) // 规范化当前搜索结果提供的内部标识。
  if (!normalizedIds.length || normalizedIds.length > 50 || normalizedIds.some((paperId) => !paperId)) throw new SearchApiError('请选择 1 至 50 篇论文生成引用图') // 保持受限图的节点请求边界。
  if (new Set(normalizedIds).size !== normalizedIds.length) throw new SearchApiError('引用图论文不能重复') // 防止同一节点重复进入布局。
  const normalizedMaxNodes = Number(maxNodes) // 将调用方提供的节点上限转换为数值。
  if (!Number.isInteger(normalizedMaxNodes) || normalizedMaxNodes < 1 || normalizedMaxNodes > 50) throw new SearchApiError('引用图节点上限必须在 1 至 50 之间') // 与后端查询参数边界保持一致。
  const normalizedEdgeTypes = Array.isArray(edgeTypes) ? [...new Set(edgeTypes.map((edgeType) => String(edgeType || '').trim()))] : [] // 仅接受调用方显式选择的已审计关系类型。
  if (normalizedEdgeTypes.some((edgeType) => edgeType !== 'cites' && edgeType !== 'same_work')) throw new SearchApiError('引用图关系类型只能是 cites 或 same_work') // 阻止关键词或模型推断关系进入接口。
  const searchParams = new URLSearchParams({ max_nodes: String(normalizedMaxNodes) }) // 构建可安全编码的只读查询参数。
  for (const paperId of normalizedIds) searchParams.append('paper_ids', paperId) // 使用重复参数传递稳定论文标识列表。
  for (const edgeType of normalizedEdgeTypes) searchParams.append('edge_types', edgeType) // 仅在页面需要版本族辅助信息时显式请求。
  let response // 保存图谱读取 HTTP 响应。
  try { // 将网络或代理异常转换为公共提示。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/graph/citations?${searchParams.toString()}`, { method: 'GET', headers: { Accept: 'application/json' } }) // 仅读取 SQLite 已保存的内部关系图。
  } catch { // 不暴露浏览器底层网络异常。
    throw new SearchApiError('无法读取引用图，请确认后端已启动') // 给出用户可执行的公共提示。
  }
  if (!response.ok) throw await parseSearchError(response) // 复用统一公共错误解析和状态码。
  try { // 校验图谱面板依赖的最小节点与边契约。
    const graph = await response.json() // 解析后端受限图响应。
    if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || typeof graph.truncated !== 'boolean') throw new SearchApiError('引用图数据不完整') // 防止页面渲染损坏关系数据。
    return graph // 返回不含外部扩展的内部事实图。
  } catch (error) { // 将 JSON 或契约错误转换为统一安全提示。
    if (error instanceof SearchApiError) throw error // 保留明确业务提示。
    throw new SearchApiError('引用图数据无法解析') // 不展示原始响应正文。
  }
}

/** 读取当前已保存论文的关键词事实路线，不调用模型或外部来源。 */
export async function getTechnicalRoutes(paperIds, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许页面和测试复用只读路线请求。
  const normalizedIds = Array.isArray(paperIds) ? paperIds.map((paperId) => String(paperId || '').trim()) : [] // 规范化内部论文标识。
  if (!normalizedIds.length || normalizedIds.length > 50 || normalizedIds.some((paperId) => !paperId) || new Set(normalizedIds).size !== normalizedIds.length) throw new SearchApiError('请选择 1 至 50 篇不重复论文生成技术路线') // 保持受限路线边界。
  const params = new URLSearchParams() // 构建安全编码查询参数。
  for (const paperId of normalizedIds) params.append('paper_ids', paperId) // 仅提交稳定标识，不传递前端关键词事实。
  let response // 保存路线读取响应。
  try { response = await fetchImpl(`${apiBaseUrl}/api/v1/routes?${params.toString()}`, { method: 'GET', headers: { Accept: 'application/json' } }) } catch { throw new SearchApiError('无法读取技术路线，请确认后端已启动') } // 将网络错误映射为安全提示。
  if (!response.ok) throw await parseSearchError(response) // 复用公共错误边界。
  const routes = await response.json() // 解析路线响应。
  if (!routes || !Array.isArray(routes.routes)) throw new SearchApiError('技术路线数据不完整') // 校验页面依赖的最小契约。
  return routes // 返回关键词事实路线。
}

/** 按运行标识读取已保存的实际用量，不触发新的检索或计费。 */
export async function getSearchRunSynthesis(runId, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许搜索页按运行标识读取事实型综合报告。
  const normalizedRunId = String(runId || '').trim() // 规范化 SSE、URL 或结果快照提供的运行标识。
  if (!normalizedRunId) throw new SearchApiError('缺少需要读取综合报告的搜索运行标识') // 阻止无效标识进入只读网络层。
  let response // 保存 HTTP 响应供统一公共错误解析。
  try { // 将浏览器网络或代理错误映射为页面可展示提示。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/search/runs/${encodeURIComponent(normalizedRunId)}/synthesis`, { method: 'GET', headers: { Accept: 'application/json' } }) // 仅读取同次 SQLite 最终结果的已汇总事实。
  } catch { // 不向页面泄露浏览器网络层细节。
    throw new SearchApiError('无法读取搜索综合报告，请确认后端已启动') // 提供可操作的安全错误。
  }
  if (!response.ok) throw await parseSearchError(response) // 复用后端稳定错误摘要。
  try { // 校验报告面板实际依赖的最小响应契约。
    const synthesis = await response.json() // 解析由后端模型序列化的事实型报告。
    if (!synthesis || typeof synthesis.run_id !== 'string' || typeof synthesis.final_paper_count !== 'number' || !Array.isArray(synthesis.sources) || !Array.isArray(synthesis.top_keywords) || !Array.isArray(synthesis.coverage_gaps) || !Array.isArray(synthesis.findings) || !Array.isArray(synthesis.follow_up_suggestions)) throw new SearchApiError('搜索综合报告数据不完整') // 阻止页面渲染不可靠或跨运行响应。
    return synthesis // 返回已通过最小契约校验的同次报告。
  } catch (error) { // 将 JSON 或字段错误转为统一公共提示。
    if (error instanceof SearchApiError) throw error // 保留可操作的契约错误。
    throw new SearchApiError('搜索综合报告无法解析') // 不显示服务端原始响应正文。
  }
}

/** 按运行标识读取已保存的实际用量，不触发新的检索或计费。 */
export async function getSearchRunUsage(runId, fetchImpl = globalThis.fetch, apiBaseUrl = DEFAULT_API_BASE_URL) { // 允许页面和测试复用只读用量请求。
  const normalizedRunId = String(runId || '').trim() // 规范化 SSE、URL 或结果快照提供的运行标识。
  if (!normalizedRunId) throw new SearchApiError('缺少需要读取用量的搜索运行标识') // 阻止向后端发起无效资源请求。
  let response // 保存用量读取 HTTP 响应。
  try { // 只发起可缓存和安全重试的 GET 请求。
    response = await fetchImpl(`${apiBaseUrl}/api/v1/usage/${encodeURIComponent(normalizedRunId)}`, { method: 'GET', headers: { Accept: 'application/json' } }) // 仅传递运行标识。
  } catch { // 网络不可达时不伪造或估算本次搜索用量。
    throw new SearchApiError('无法读取搜索用量，请确认后端已启动') // 提供用户可执行的公共提示。
  }
  if (!response.ok) throw await parseSearchError(response) // 复用稳定的后端公共错误边界。
  try { // 校验搜索页展示所依赖的最小统计字段。
    const usage = await response.json() // 解析由后端模型序列化的只读快照。
    if (!usage || typeof usage.run_id !== 'string' || typeof usage.api_call_count !== 'number' || typeof usage.token_usage !== 'number' || typeof usage.latency_ms !== 'number' || typeof usage.cache_hits !== 'number' || !Array.isArray(usage.selected_sources)) throw new SearchApiError('搜索用量数据不完整') // 防止不完整响应误导用户。
    return usage // 返回同次运行的真实观测数据。
  } catch (error) { // 将 JSON 解析或契约错误转换为统一安全提示。
    if (error instanceof SearchApiError) throw error // 保留可直接展示的业务错误。
    throw new SearchApiError('搜索用量数据无法解析') // 不展示代理页或内部响应正文。
  }
}

/** 将 SSE 单帧解析为已净化的事件 data JSON。 */
function parseSseFrame(frame) { // 接收不含结尾空行的 SSE 文本帧。
  const dataLine = frame.split('\n').find((line) => line.startsWith('data:')) // 仅消费服务端标准 data 行。
  if (!dataLine) return null // 心跳或非数据帧无需触发页面更新。
  try { // 事件 JSON 必须能解析才允许进入页面状态。
    return JSON.parse(dataLine.slice(5).trim()) // 删除 data 前缀后恢复公开事件对象。
  } catch { // 非法事件不应让长时搜索页面崩溃。
    throw new SearchApiError('检索进度事件无法解析') // 返回不含原始事件正文的稳定错误。
  }
}

/** 将错误响应转换为统一安全前端错误。 */
async function parseSearchError(response) { // 复用 REST 与 SSE 的错误消息处理逻辑。
  let message = '论文检索暂时不可用，请稍后重试' // 提供安全默认消息。
  try { // 优先读取 FastAPI 已净化的 detail 字段。
    const errorBody = await response.json() // 解析 JSON 错误响应。
    if (typeof errorBody.detail === 'string') message = errorBody.detail // 仅展示后端提供的公共说明。
  } catch { // 非 JSON 响应继续使用默认消息。
    // 不读取 HTML 代理页或底层异常正文，避免泄露无关内容。
  }
  return new SearchApiError(message, response.status) // 返回调用方可统一捕获的错误对象。
}

/** 在浏览器与 Node 测试环境中安全深复制纯 JSON 查询意图。 */
function structuredCloneSafe(value) { // 接收仅包含 JSON 类型的 API 响应对象。
  if (typeof globalThis.structuredClone === 'function') { // 优先使用原生结构化复制。
    try { return globalThis.structuredClone(value) } catch { /* Vue 响应式 Proxy 无法原生复制时回退为 JSON 深复制。 */ }
  }
  return JSON.parse(JSON.stringify(value)) // 兼容旧环境和 Vue 响应式嵌套字段。
}
