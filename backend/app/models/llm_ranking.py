"""定义 LLM 论文核验、推荐与最终精排阶段的数据契约。"""

from typing import Literal  # 限制约束核验状态为前端可稳定消费的枚举。

from pydantic import BaseModel, Field  # 校验外部模型输出和服务阶段结果。

from backend.app.models.paper import PaperRecord  # 保存附加核验信息后的最终论文记录。


ConstraintMatchStatus = Literal["satisfied", "uncertain", "not_satisfied"]  # 区分满足、证据不足和明确不满足三类状态。


class LlmPaperAssessment(BaseModel):
    """保存 LLM 对单篇论文的结构化相关性与约束核验结果。"""

    paper_id: str = Field(min_length=1)  # 使用输入论文稳定标识关联模型输出，禁止按数组位置猜测。
    relevance_score: float = Field(ge=0.0, le=1.0)  # 保存仅用于本批候选比较的归一化相关性分数。
    constraint_status: ConstraintMatchStatus  # 保存硬约束总体核验状态。
    evidence: list[str] = Field(default_factory=list)  # 保存模型从公开论文元数据中摘取的短证据片段。
    recommendation_reason: str = Field(min_length=1, max_length=500)  # 保存面向用户的简短推荐理由。


class LlmAssessmentBatch(BaseModel):
    """保存一次供应商调用返回的论文核验列表与 Token 统计。"""

    assessments: list[LlmPaperAssessment] = Field(default_factory=list)  # 保存经适配层结构校验的逐篇核验结果。
    model_name: str = Field(min_length=1)  # 保存实际响应声明的模型名称便于成本审计。
    prompt_tokens: int = Field(default=0, ge=0)  # 保存供应商报告的输入 Token 数量。
    completion_tokens: int = Field(default=0, ge=0)  # 保存供应商报告的输出 Token 数量。
    estimated_cost_cny: float = Field(default=0.0, ge=0.0)  # 保存本次供应商 usage 对应的人民币费用估算。
    peak_pricing_applied: bool = False  # 标记本次调用是否采用工作时间两倍费率。


class LlmRankingResult(BaseModel):
    """保存 LLM 最终精排、约束淘汰、候选截断和降级信息。"""

    papers: list[PaperRecord] = Field(default_factory=list)  # 保存不超过最终目标数量的论文结果。
    input_count: int = Field(default=0, ge=0)  # 保存进入 LLM 阶段的 Cross Encoder 候选数。
    truncated_count: int = Field(default=0, ge=0)  # 保存通过核验但超出最终结果上限的候选数。
    rejected_count: int = Field(default=0, ge=0)  # 保存被 LLM 明确判定不满足硬约束的候选数。
    model_name: str = Field(min_length=1)  # 保存配置或实际使用的 LLM 名称。
    call_count: int = Field(default=0, ge=0)  # 保存已尝试的 DeepSeek 小批次数，失败批次也属于真实调用尝试。
    prompt_tokens: int = Field(default=0, ge=0)  # 保存本阶段输入 Token 数量。
    completion_tokens: int = Field(default=0, ge=0)  # 保存本阶段输出 Token 数量。
    estimated_cost_cny: float = Field(default=0.0, ge=0.0)  # 保存全部成功核验批次累计的人民币费用估算。
    peak_pricing_applied: bool = False  # 标记任一成功核验批次是否应用了峰时费率。
    ranking_error: str | None = None  # 保存不含密钥、响应正文和内部路径的安全降级摘要。
