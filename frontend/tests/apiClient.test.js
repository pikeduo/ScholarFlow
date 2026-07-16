import assert from 'node:assert/strict' // 使用 Node 内置严格断言验证公共请求边界。
import test from 'node:test' // 使用零依赖内置测试运行器声明用例。

import { requestApiJson, requestApiResponse } from '../src/services/apiClient.js' // 导入待测的无业务语义请求内核。

class TestApiError extends Error { // 构造可断言消息和状态码的领域错误替身。
  constructor(message, status = null) { // 接收公共展示消息与可选 HTTP 状态。
    super(message) // 保存错误消息。
    this.status = status // 保存 HTTP 状态供调用方断言。
  }
}

function createConfig(overrides = {}) { // 构造所有测试共享的明确错误文案与离线 fetch 依赖。
  return { fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }), apiBaseUrl: 'http://test.local', ErrorType: TestApiError, networkMessage: '网络不可用', unavailableMessage: '服务暂不可用', notFoundMessage: '资源不存在', invalidJsonMessage: '响应无法解析', ...overrides } // 保持每个用例只覆盖与其相关的配置项。
}

test('requestApiJson 保留调用方路径、请求配置并返回成功 JSON', async () => { // 验证内核不改写业务服务提供的版本化路径或方法。
  let capturedUrl = '' // 保存实际请求地址。
  let capturedOptions = null // 保存实际请求配置。
  const config = createConfig({ fetchImpl: async (url, options) => { capturedUrl = url; capturedOptions = options; return { ok: true, status: 200, json: async () => ({ item: 'saved' }) } } }) // 注入不访问网络的成功响应替身。

  const result = await requestApiJson('/api/v1/example', { method: 'POST', headers: { Accept: 'application/json' } }, config) // 发起最小版本化 JSON 请求。

  assert.equal(capturedUrl, 'http://test.local/api/v1/example') // 验证 API 根地址与相对路径按原样拼接。
  assert.equal(capturedOptions.method, 'POST') // 验证请求方法由业务服务保持拥有。
  assert.deepEqual(result, { item: 'saved' }) // 验证成功 JSON 原样交给业务服务执行字段校验。
})

test('requestApiResponse 兼容 detail 与标准 error.message 并保留状态码', async () => { // 验证迁移期间两类后端安全错误结构都不会丢失可展示消息。
  const detailConfig = createConfig({ fetchImpl: async () => ({ ok: false, status: 503, json: async () => ({ detail: '检索服务维护中' }) }) }) // 构造当前 FastAPI HTTPException 错误结构。
  const standardConfig = createConfig({ fetchImpl: async () => ({ ok: false, status: 404, json: async () => ({ error: { message: '未找到保存快照' } }) }) }) // 构造阶段六规划中的标准错误结构。

  await assert.rejects(() => requestApiResponse('/api/v1/example', { method: 'GET' }, detailConfig), (error) => error instanceof TestApiError && error.message === '检索服务维护中' && error.status === 503) // 验证优先使用 FastAPI detail。
  await assert.rejects(() => requestApiResponse('/api/v1/example', { method: 'GET' }, standardConfig), (error) => error instanceof TestApiError && error.message === '未找到保存快照' && error.status === 404) // 验证兼容标准 error.message。
})

test('requestApiJson 为网络、无正文和无效 JSON 保留调用方安全边界', async () => { // 验证内核不会泄露浏览器异常或错误响应正文。
  const networkConfig = createConfig({ fetchImpl: async () => { throw new Error('socket reset') } }) // 构造浏览器底层网络失败。
  const emptyConfig = createConfig({ fetchImpl: async () => ({ ok: true, status: 204 }), allowEmpty: true }) // 构造删除等无正文成功响应。
  const invalidJsonConfig = createConfig({ fetchImpl: async () => ({ ok: true, status: 200, json: async () => { throw new Error('invalid json') } }) }) // 构造成功状态但正文无法解析的代理异常。

  await assert.rejects(() => requestApiJson('/api/v1/example', { method: 'GET' }, networkConfig), /网络不可用/) // 验证网络异常转换为调用方安全文案。
  assert.equal(await requestApiJson('/api/v1/example', { method: 'DELETE' }, emptyConfig), null) // 验证允许的 204 不触发 JSON 解析。
  await assert.rejects(() => requestApiJson('/api/v1/example', { method: 'GET' }, invalidJsonConfig), /响应无法解析/) // 验证无效 JSON 使用调用方指定文案。
})
