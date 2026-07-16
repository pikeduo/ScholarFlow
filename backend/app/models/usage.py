"""定义搜索运行用量读取的稳定响应契约。"""

from pydantic import BaseModel  # 提供只读用量响应模型。


class SearchRunUsage(BaseModel):
    """汇总已保存运行的 API、Token、费用、耗时和缓存统计。

    所有字段均来自 ``SearchRunState`` 的同次持久化快照，不承担实时计费或预测职责。
    """

    run_id: str  # 返回可与搜索页 URL 和 SSE 事件关联的运行标识。
    api_call_count: int  # 返回运行累计外部 API 调用数量。
    token_usage: int  # 返回运行累计 LLM Token 使用量。
    estimated_cost_cny: float  # 返回调用时按供应商 usage 与价表保存的人民币费用估算。
    peak_pricing_applied: bool  # 返回是否至少一次应用工作时间两倍费率。
    cost_is_estimate: bool = True  # 明确余额抵扣、赠送额度和税费不在本地快照内。
    latency_ms: int  # 返回从工作流记录的累计耗时毫秒数。
    cache_hits: int  # 返回检索和规划缓存累计命中次数。
    current_round: int  # 返回已完成或正在执行的当前轮次。
    max_rounds: int  # 返回本次运行允许的最大轮次数。
    selected_sources: list[str]  # 返回本次实际选择的学术来源。
    stop_reason: str | None = None  # 返回完成或预算停止等可展示原因。
