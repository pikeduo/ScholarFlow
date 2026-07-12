"""提供多轮搜索控制器和 SSE 路由共享的进度事件发布边界。"""

import asyncio  # 使用同一事件循环内的队列连接控制器和 StreamingResponse。
from typing import Protocol  # 定义可替换为 Redis Stream 的事件发布协议。

from backend.app.models.search_event import SearchProgressEvent  # 传递不含敏感内容的稳定事件契约。


class SearchRunEventPublisher(Protocol):
    """定义控制器发布搜索进度事件所需的最小同步接口。"""

    def publish(self, event: SearchProgressEvent) -> None:
        """发布一条已校验的搜索进度事件。"""
        ...  # 实现可替换为 Redis Stream、短期队列或测试替身。


class InMemorySearchRunEventPublisher:
    """使用单次请求私有 asyncio 队列向 SSE 响应实时传递事件。"""

    def __init__(self, max_events: int = 100) -> None:
        """创建有界事件队列，防止慢客户端无限占用内存。

        参数：
            max_events：单次流允许暂存的最大轻量事件数。
        异常：
            ValueError：队列上限不是正数时抛出。
        """
        if max_events < 1:  # 零容量队列无法可靠传递控制器事件。
            raise ValueError("max_events 必须大于零")  # 在路由装配阶段暴露稳定配置错误。
        self._queue: asyncio.Queue[SearchProgressEvent] = asyncio.Queue(maxsize=max_events)  # 创建仅服务当前 SSE 请求的有界队列。

    def publish(self, event: SearchProgressEvent) -> None:
        """非阻塞写入事件，慢客户端时丢弃最旧进度而保留最新状态。

        参数：
            event：控制器生成的轻量、已校验进度事件。
        """
        if self._queue.full():  # 避免控制器因浏览器读取慢而阻塞来源调用和停止判断。
            self._queue.get_nowait()  # 丢弃最旧的中间进度事件，最终状态仍会再次发布。
        self._queue.put_nowait(event)  # 在当前事件循环立即提交最新事件。

    async def next_event(self) -> SearchProgressEvent:
        """等待并返回下一条可发送的搜索进度事件。"""
        return await self._queue.get()  # 由 StreamingResponse 异步生成器消费队列。

    def empty(self) -> bool:
        """返回当前是否仍有未发送事件。"""
        return self._queue.empty()  # 用于任务结束后安全关闭 SSE 响应。
