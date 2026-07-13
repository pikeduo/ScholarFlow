# Redis Stream 与搜索 SSE 评估

## 决策

当前不引入 Redis Stream，保留单次 SSE 请求私有的 `InMemorySearchRunEventPublisher`。

## 依据

- 多轮搜索任务由 `_create_multi_round_sse_response` 在发起 SSE 的同一进程内创建并执行，事件生产者与消费者共享同一事件循环和有界队列。
- 运行状态与最终结果已经写入 SQLite；浏览器刷新后通过 `run_id` 恢复状态，并在完成后读取同次最终结果，不需要通过事件流补齐论文数据。
- 目前没有多 worker、跨实例任务执行器或独立后台任务边界。仅替换队列为 Redis Stream 不能让任务跨实例继续执行，也不能让断线客户端可靠重放事件。
- 现有事件契约有 `event_id`，但 SSE 接口尚未接收 `Last-Event-ID` 或提供按 `run_id` 重订阅端点，因此直接增加 Stream 会形成只能写入、不能正确续传的存储负担。

## 启用条件

只有同时满足以下条件时，才将事件发布器替换为 Redis Stream：

1. 搜索执行已从 HTTP SSE 请求中解耦为可跨进程运行的后台任务。
2. 事件订阅接口支持按 `run_id` 与 `Last-Event-ID` 使用 `XREAD` 续传。
3. 每个运行的 Stream 设置最大长度和 TTL，并在终态后保留有限重放窗口。
4. SQLite 继续保存终态状态与结果，Redis Stream 仅保存可丢失的过程事件。
5. 增加 Redis 不可用回退、断线续传、终态顺序和多进程发布/订阅的离线测试。

## 后续顺序

先由用户环境完成现有文献搜索端到端验收；确认确有多进程部署与事件续传需求后，再单独规划后台任务和 Redis Stream 闭环。
