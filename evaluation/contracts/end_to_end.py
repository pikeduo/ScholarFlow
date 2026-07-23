"""定义固定 PaSa 端到端评测的在线归档与离线报告契约。"""

from typing import Literal  # 限制运行状态，避免失败查询被静默省略。

from pydantic import BaseModel, ConfigDict, Field  # 提供严格、可序列化的评测归档模型。

from evaluation.contracts.common import EvaluationPaper, RelationRecord  # 复用论文与关系事实边界。


RunStatus = Literal["completed", "failed", "cancelled", "timeout", "transport_error", "invalid_response"]  # 覆盖生产终态与评测客户端失败。


class LlmStageUsage(BaseModel):
    """保存一个 LLM 阶段的已观测调用、Token 与成本；未知值保持空。"""

    call_count: int | None = Field(default=None, ge=0)  # 失败调用也应计入已观测的实际尝试。
    input_tokens: int | None = Field(default=None, ge=0)  # 保存供应商返回的输入 Token。
    output_tokens: int | None = Field(default=None, ge=0)  # 保存供应商返回的输出 Token。
    total_tokens: int | None = Field(default=None, ge=0)  # 保存供应商或安全加和后的总 Token。
    estimated_cost_cny: float | None = Field(default=None, ge=0)  # 保存运行时冻结的成本估算。


class EndToEndUsage(BaseModel):
    """保存一次自然语言搜索的原始效率观测，不把缺失伪装为零。"""

    academic_api_calls: int | None = Field(default=None, ge=0)  # 保存生产快照的逻辑学术调用数。
    actual_http_requests: int | None = Field(default=None, ge=0)  # 当前生产快照未提供时保持空。
    retry_count: int | None = Field(default=None, ge=0)  # 当前生产快照未提供时保持空。
    rate_limit_count: int | None = Field(default=None, ge=0)  # 当前生产快照未提供时保持空。
    latency_ms: float | None = Field(default=None, ge=0)  # 保存从自然语言入口开始的端到端耗时。
    total_estimated_cost_cny: float | None = Field(default=None, ge=0)  # 保存使用接口返回的冻结总费用。
    query_agent: LlmStageUsage = Field(default_factory=LlmStageUsage)  # 保存 Query Agent 单次调用观测。
    query_evolution: LlmStageUsage = Field(default_factory=LlmStageUsage)  # 保存覆盖缺口策略调用观测。
    final_verification: LlmStageUsage = Field(default_factory=LlmStageUsage)  # 保存最终核验与理由合并调用观测。
    llm_total_tokens: int | None = Field(default=None, ge=0)  # 保存运行快照累计 LLM Token。


class EndToEndRunRecord(BaseModel):
    """归档一条固定查询的完整终态、论文、关系和安全异常摘要。"""

    model_config = ConfigDict(extra="forbid")  # 防止在线响应字段变化被静默写入评测产物。

    query_id: str = Field(min_length=1)  # 关联固定 PaSa GoldQuery 标识。
    run_id: str | None = None  # 运行未创建或网络失败时允许为空。
    status: RunStatus  # 保留所有失败、超时和空结果查询的分母位置。
    papers: list[EvaluationPaper] = Field(default_factory=list, max_length=20)  # 保存最终 Top 20 或更少的真实论文。
    usage: EndToEndUsage = Field(default_factory=EndToEndUsage)  # 保存不重新估算的已观测效率数据。
    stop_reason: str | None = None  # 保存生产停止原因或客户端超时原因。
    degraded_sources: list[str] = Field(default_factory=list)  # 保存来源降级名称。
    safe_errors: list[str] = Field(default_factory=list)  # 保存已净化的错误摘要。
    graph_requested: bool = False  # 标记是否已按最终论文集合请求关系图。
    graph_generated: bool = False  # 标记是否取得合法图响应。
    graph_node_ids: list[str] = Field(default_factory=list)  # 保存图节点标识供离线悬空边校验。
    relations: list[RelationRecord] = Field(default_factory=list)  # 保存仅来自生产图接口的事实边。


class EndToEndEvaluationSummary(BaseModel):
    """保存 PaSa 固定 20 条初步评测的机读汇总。"""

    schema_version: Literal["pasa-end-to-end-evaluation-v1"] = "pasa-end-to-end-evaluation-v1"  # 冻结本次新报告格式。
    disclaimer: Literal["PaSa AutoScholarQuery dev固定20条初步评测，非完整数据集成绩，非赛事官方成绩"] = "PaSa AutoScholarQuery dev固定20条初步评测，非完整数据集成绩，非赛事官方成绩"  # 强制写入用户指定说明。
    query_count: int = Field(ge=0)  # 保存固定评测分母。
    retrieval: dict[str, object]  # 保存 P/R/F1 宏微汇总与命中覆盖统计。
    efficiency: dict[str, object]  # 保存原始效率观测与缺失字段。
    structure: dict[str, object]  # 保存列表、字段和图关系的确定性统计。
    warnings: list[str] = Field(default_factory=list)  # 保存不可观测指标与非官方口径提示。
