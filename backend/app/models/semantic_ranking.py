"""定义 BGE-M3 语义粗排阶段的稳定输出契约。"""

from pydantic import BaseModel, Field  # 提供语义粗排输出的结构化校验。

from backend.app.models.paper import PaperRecord  # 保存按语义分数排序并截断的论文记录。


class SemanticRankingResult(BaseModel):
    """保存 BGE-M3 粗排后的候选、截断统计与安全降级状态。"""

    papers: list[PaperRecord] = Field(default_factory=list)  # 保存按语义分数和稳定次级键排序后的候选论文。
    input_count: int = Field(ge=0)  # 记录进入粗排前的规则过滤候选数量。
    truncated_count: int = Field(ge=0)  # 记录因候选上限未进入后续阶段的论文数量。
    model_name: str = Field(min_length=1)  # 保存实际配置的语义模型名称。
    ranking_error: str | None = None  # 保存不含路径、密钥和底层异常的安全降级摘要。
