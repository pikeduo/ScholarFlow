/**
 * 执行前端版本化 API 请求，并在服务模块保留各自领域错误类型的前提下统一 HTTP 边界。
 *
 * @param {string} path 相对于 API 根地址的版本化资源路径。
 * @param {RequestInit} options 请求方法、头部和可选 JSON 正文。
 * @param {{ fetchImpl: typeof fetch, apiBaseUrl: string, ErrorType: new (message: string, status?: number | null) => Error, networkMessage: string, unavailableMessage: string, notFoundMessage?: string, unsupportedNetworkMessage?: string }} config 请求依赖和领域错误文案。
 * @returns {Promise<Response>} 仅返回已通过网络和 HTTP 状态校验的原始响应。
 * @throws {Error} 网络、HTTP 状态或运行环境异常均转换为调用方指定的领域错误。
 */
export async function requestApiResponse(path, options, config) { // 只负责请求边界，不解析各业务模块不同的成功响应契约。
  const { fetchImpl, apiBaseUrl, ErrorType, networkMessage, unavailableMessage, notFoundMessage, unsupportedNetworkMessage } = config // 解构调用方显式提供的依赖与安全文案。
  if (typeof fetchImpl !== 'function') throw new ErrorType(unsupportedNetworkMessage || networkMessage) // 在发起请求前处理缺失 fetch 的旧浏览器或测试环境。
  let response // 保存成功建立连接后的原始 HTTP 响应。
  try { // 将浏览器网络、代理和服务未启动异常隔离为公共提示。
    response = await fetchImpl(`${apiBaseUrl}${path}`, options) // 所有业务服务仍只传递版本化相对路径。
  } catch { // 不将浏览器底层异常或网络实现细节暴露到页面。
    throw new ErrorType(networkMessage) // 使用调用方按业务场景定义的可操作提示。
  }
  if (response.ok) return response // 成功响应交由业务服务完成其最小字段契约校验。
  throw await createApiHttpError(response, { ErrorType, unavailableMessage, notFoundMessage }) // 非成功响应统一读取安全错误结构并保留状态码。
}

/**
 * 执行 JSON API 请求，并在允许无正文响应时避免解析 204。
 *
 * @param {string} path 相对于 API 根地址的版本化资源路径。
 * @param {RequestInit} options 请求方法、头部和可选 JSON 正文。
 * @param {{ fetchImpl: typeof fetch, apiBaseUrl: string, ErrorType: new (message: string, status?: number | null) => Error, networkMessage: string, unavailableMessage: string, notFoundMessage?: string, unsupportedNetworkMessage?: string, allowEmpty?: boolean, invalidJsonMessage: string }} config 请求依赖、错误文案和无正文策略。
 * @returns {Promise<object | null>} 已解析 JSON，或由调用方允许的无正文结果。
 * @throws {Error} HTTP 或 JSON 错误均转换为调用方指定的领域错误。
 */
export async function requestApiJson(path, options, config) { // 在原始响应封装之上复用通用 JSON 解析边界。
  const response = await requestApiResponse(path, options, config) // 先确保请求已通过网络和 HTTP 状态校验。
  if (config.allowEmpty || response.status === 204) return null // 删除等无正文成功响应不得进入 JSON 解析。
  try { // 仅解析成功响应正文，避免服务模块重复 try/catch。
    return await response.json() // 返回交由调用方校验字段的原始 JSON 数据。
  } catch { // 代理错误页或空正文不能直接导致页面崩溃。
    throw new config.ErrorType(config.invalidJsonMessage) // 保持各服务已有的安全解析失败文案。
  }
}

/**
 * 将 FastAPI 旧 detail 或标准 error.message 转换为调用方指定的安全领域错误。
 *
 * @param {Response} response 非成功 HTTP 响应。
 * @param {{ ErrorType: new (message: string, status?: number | null) => Error, unavailableMessage: string, notFoundMessage?: string }} config 错误构造器及回退文案。
 * @returns {Promise<Error>} 含状态码且可供页面展示的领域错误。
 */
export async function createApiHttpError(response, { ErrorType, unavailableMessage, notFoundMessage }) { // 同时兼容现有 FastAPI detail 和阶段六规划的标准 error 对象。
  let message = response.status === 404 && notFoundMessage ? notFoundMessage : unavailableMessage // 先按状态选择业务模块的安全默认文案。
  try { // 优先读取后端已净化的结构化错误，而非原始文本或 HTML。
    const errorBody = await response.json() // 解析 FastAPI 或统一错误响应 JSON。
    if (typeof errorBody?.detail === 'string') message = errorBody.detail // 兼容当前 FastAPI HTTPException 的 detail 格式。
    else if (typeof errorBody?.error?.message === 'string') message = errorBody.error.message // 兼容阶段六定义的统一 error.message 格式。
  } catch { // 非 JSON 代理页或空响应继续使用安全默认文案。
    // 不读取原始响应正文，避免展示内部路径、HTML 或网关信息。
  }
  return new ErrorType(message, response.status) // 将可展示消息与状态码交由领域服务暴露给页面。
}
