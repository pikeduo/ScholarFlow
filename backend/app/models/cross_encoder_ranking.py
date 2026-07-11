"""定义 Cross Encoder 重排阶段的稳定输出契约。"""

from pydantic import BaseModel, Field  # 提供重排结果的结构化校验。

from backend.app.models.paper import PaperRecord  # 保存精细相关性排序并截断后的论文记录。


class CrossEncoderRankingResult(BaseModel):
    """保存 Cross Encoder 重排后的候选、截断统计与安全降级状态。"""

    papers: list[PaperRecord] = Field(default_factory=list)  # 保存按 Cross Encoder 分数排序的候选论文。
    input_count: int = Field(ge=0)  # 记录进入 Cross Encoder 前的 BGE-M3 候选数量。
    truncated_count: int = Field(ge=0)  # 记录因重排候选上限未进入 LLM 核验的论文数量。
    model_name: str = Field(min_length=1)  # 保存实际配置的重排模型名称。
    ranking_error: str | None = None  # 保存不含底层模型或设备细节的安全降级摘要。
