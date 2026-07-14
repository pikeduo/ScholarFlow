"""定义覆盖缺口驱动的查询演化结果契约。"""

from pydantic import BaseModel, Field  # 提供演化结果字段的稳定校验。

from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 复用可直接进入来源适配器的查询契约。


class QueryEvolutionResult(BaseModel):
    """保存一次无副作用查询演化生成的补充子查询与拒绝原因。

    属性：
        query_intent：追加待执行子查询后的查询意图副本。
        generated_subqueries：本次新生成且通过去重检查的子查询。
        skipped_gap_count：因不可查询、重复或达到上限而未生成查询的缺口数量。
        warnings：供工作流和前端展示的安全跳过原因。
    """

    query_intent: QueryIntent  # 保存不修改调用方原对象的待执行查询意图副本。
    generated_subqueries: list[QuerySubquery] = Field(default_factory=list)  # 保存本轮可交给来源适配器的新增查询。
    skipped_gap_count: int = Field(default=0, ge=0)  # 统计未能形成新查询的缺口数量。
    warnings: list[str] = Field(default_factory=list)  # 保存不含原始敏感查询的可展示跳过摘要。
    strategy_reason: str | None = None  # 保存 LLM 策略产生的简短理由，缺失表示未调用或已降级。
    strategy_model_name: str | None = None  # 保存实际执行策略的模型名称供用量审计。
    strategy_prompt_tokens: int = Field(default=0, ge=0)  # 保存本轮策略模型输入 Token。
    strategy_completion_tokens: int = Field(default=0, ge=0)  # 保存本轮策略模型输出 Token。
