"""定义 LangGraph 工作流和持久化层共享的 SearchRunState 契约。"""

from typing import Literal  # 限制工作流运行状态的稳定取值。
from uuid import uuid4  # 生成不依赖数据库的临时搜索运行标识。

from pydantic import BaseModel, Field, model_validator  # 提供工作流状态的字段和跨字段校验。

from backend.app.models.paper import PaperRecord, PaperSource  # 保存多源规范化论文和来源状态。
from backend.app.models.query_intent import QueryIntent, SearchMode  # 保存已规划的查询意图和检索模式。


SearchRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]  # 限制首版工作流的可追踪状态集合。


class SearchRunState(BaseModel):
    """保存一次有状态搜索运行的输入、中间结果、预算和停止信息。

    属性：
        run_id：可用于 REST 查询、SSE 订阅和恢复的唯一标识。
        query_intent：所有下游节点共用的已校验查询意图。
        search_mode：标准或深度检索模式。
        current_round：当前已完成或正在执行的检索轮次。
        max_rounds：本次运行允许的最大检索轮次。
        selected_sources：当前轮次已选择的来源。
        executed_subqueries：已执行过的子查询，避免重复调用。
        normalized_papers：完成统一映射的候选论文。
        candidate_ids：进入排序阶段的候选内部标识。
        api_call_count：本次运行发生的外部 API 调用次数。
        token_usage：本次运行累计的 LLM Token 数量。
        cost_usd：本次运行累计估算或实际费用。
        latency_ms：本次运行累计耗时。
        cache_hits：本次运行缓存命中次数。
        warnings：安全可展示的降级或约束提示。
        errors：已净化的错误摘要。
        degraded_sources：本次运行不可用但未阻塞整体结果的来源。
        stop_reason：完成、预算触顶或边际收益不足等停止原因。
        status：当前工作流状态文本。
    """

    run_id: str = Field(default_factory=lambda: str(uuid4()))  # 生成可跨 REST 和 SSE 关联的唯一标识。
    query_intent: QueryIntent  # 固化当前运行的查询规划结果。
    search_mode: SearchMode  # 保存实际使用的标准或深度模式。
    current_round: int = Field(default=0, ge=0)  # 从尚未执行任何检索轮次的零开始计数。
    max_rounds: int = Field(ge=1, le=3)  # 约束首版最多执行三轮检索。
    selected_sources: list[PaperSource] = Field(default_factory=list)  # 保存已选择的固定或动态来源。
    executed_subqueries: list[str] = Field(default_factory=list)  # 保存已执行查询以避免重复搜索。
    normalized_papers: list[PaperRecord] = Field(default_factory=list)  # 保存进入融合阶段的统一论文记录。
    candidate_ids: list[str] = Field(default_factory=list)  # 保存进入排序与核验阶段的候选标识。
    api_call_count: int = Field(default=0, ge=0)  # 记录本次运行的外部 API 调用数量。
    token_usage: int = Field(default=0, ge=0)  # 记录本次运行累计的 LLM Token 数。
    cost_usd: float = Field(default=0.0, ge=0.0)  # 记录本次运行累计费用。
    latency_ms: int = Field(default=0, ge=0)  # 记录本次运行累计端到端耗时。
    cache_hits: int = Field(default=0, ge=0)  # 记录检索和规划缓存的命中次数。
    warnings: list[str] = Field(default_factory=list)  # 保存不含密钥和完整敏感查询的警告。
    errors: list[str] = Field(default_factory=list)  # 保存不含堆栈和请求参数的错误摘要。
    degraded_sources: list[PaperSource] = Field(default_factory=list)  # 保存发生故障但允许整体降级的来源。
    stop_reason: str | None = None  # 保存确定性停止条件或最终失败原因。
    status: SearchRunStatus = "pending"  # 默认标记为尚未开始执行的运行。

    @model_validator(mode="after")
    def validate_round_boundary(self) -> "SearchRunState":
        """确保当前检索轮次不会超出本次运行允许的最大轮次。

        返回：
            SearchRunState：通过轮次边界校验的当前状态。
        异常：
            ValueError：当前轮次超过最大轮次时抛出。
        """
        if self.current_round > self.max_rounds:  # 防止工作流在预算守卫失效时继续无限循环。
            raise ValueError("current_round 不能超过 max_rounds")  # 返回供工作流和 API 层处理的稳定错误。
        return self  # 返回满足轮次边界的状态对象。
