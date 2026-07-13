"""定义搜索运行用量读取的稳定响应契约。"""
from pydantic import BaseModel  # 提供只读用量响应模型。
class SearchRunUsage(BaseModel):
    """汇总已保存运行的 API、Token、费用、耗时和缓存统计。"""
    run_id: str
    api_call_count: int
    token_usage: int
    cost_usd: float
    latency_ms: int
    cache_hits: int
    current_round: int
    max_rounds: int
    selected_sources: list[str]
    stop_reason: str | None = None
