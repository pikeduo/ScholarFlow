"""定义跨来源论文融合阶段的输入统计与稳定输出契约。"""

from pydantic import BaseModel, Field  # 提供融合结果的结构化校验与稳定序列化。

from backend.app.models.paper import PaperRecord  # 保存已经完成跨来源字段合并的论文记录。


class PaperFusionResult(BaseModel):
    """保存一次论文身份融合的输出记录与可观测统计。

    属性：
        papers：每个身份组融合后的规范化论文记录。
        input_count：进入融合阶段的原始论文记录数。
        fused_count：融合后保留的身份组数量。
        merged_count：被并入其他身份组的原始记录数量。
        work_family_count：本次输出中可识别版本族的唯一数量。
    """

    papers: list[PaperRecord] = Field(default_factory=list)  # 保存保持首次出现组顺序的融合论文列表。
    input_count: int = Field(ge=0)  # 记录进入身份解析前的原始论文数量。
    fused_count: int = Field(ge=0)  # 记录跨来源身份组融合后的论文数量。
    merged_count: int = Field(ge=0)  # 记录相较输入被合并掉的重复记录数量。
    work_family_count: int = Field(ge=0)  # 记录输出中版本族标识的唯一数量。
