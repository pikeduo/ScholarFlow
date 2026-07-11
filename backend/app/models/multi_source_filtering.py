"""定义多源融合论文规则过滤阶段的稳定输出契约。"""

from pydantic import BaseModel, Field  # 提供过滤结果的结构化校验与稳定序列化。

from backend.app.models.paper import PaperRecord  # 保存通过规则过滤的融合论文记录。


class MultiSourceFilterResult(BaseModel):
    """保存融合论文按 QueryIntent 执行规则过滤后的记录和统计。

    属性：
        papers：通过全部确定性约束的融合论文。
        input_count：进入过滤阶段的融合论文数量。
        filtered_count：因规则约束被移除的论文数量。
        filter_reason_counts：按首个未通过规则汇总的移除数量。
        work_family_count：保留论文中唯一版本族的数量。
    """

    papers: list[PaperRecord] = Field(default_factory=list)  # 保存保持融合输入相对顺序的保留论文。
    input_count: int = Field(ge=0)  # 记录进入规则过滤前的融合论文数量。
    filtered_count: int = Field(ge=0)  # 记录被确定性规则移除的论文数量。
    filter_reason_counts: dict[str, int] = Field(default_factory=dict)  # 保存每条论文首个不满足规则的安全统计。
    work_family_count: int = Field(ge=0)  # 保存过滤后结果的唯一版本族数量。
