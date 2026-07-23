"""定义已确认 PaSa AutoScholarQuery JSONL 记录的本地输入契约。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator  # 对已确认字段版本执行严格校验。

from evaluation.contracts.common import JsonScalar  # 限制来源元数据可安全扁平化为评测归档标量。


class PasaRawQuery(BaseModel):
    """保存 PaSa AutoScholarQuery/dev.jsonl 的一条原始查询记录。

    属性：
        qid：PaSa 原始查询标识。
        question：PaSa 提供的自然语言问题。
        answer：按顺序给出的相关论文标题。
        answer_arxiv_id：与标题同位置对应的可选 arXiv 标识。
        source_meta：PaSa 提供的标量来源元数据。
    """

    model_config = ConfigDict(extra="forbid")  # 当前适配器只接受已确认的 AutoScholarQuery 字段版本。

    qid: str = Field(min_length=1)  # 保存可回溯到 PaSa 原始数据的稳定查询标识。
    question: str = Field(min_length=1)  # 保存可直接进入 GoldQuery 的原始问题文本。
    answer: list[str] = Field(default_factory=list)  # 保存相关论文标题并保留 PaSa 给出的顺序。
    answer_arxiv_id: list[str] = Field(default_factory=list)  # 保存与标题按索引配对的 arXiv 标识。
    source_meta: dict[str, JsonScalar] = Field(default_factory=dict)  # 保存可安全写入 GoldQuery metadata 的来源标量。

    @model_validator(mode="after")
    def validate_pasa_boundaries(self) -> "PasaRawQuery":
        """校验可审计的查询文本、论文标题和标题/标识配对关系。"""
        if not self.qid.strip():  # 仅空白 qid 无法建立稳定数据集命名空间。
            raise ValueError("qid 不能只包含空白")  # 在转换前拒绝不可追溯的来源记录。
        if not self.question.strip():  # 仅空白问题不能构成可评分查询。
            raise ValueError("question 不能只包含空白")  # 补足 Pydantic 最小长度未覆盖的空白边界。
        if any(not title.strip() for title in self.answer):  # 空白论文标题无法作为保守身份回退依据。
            raise ValueError("answer 不能包含空白论文标题")  # 要求用户确认或修复来源数据。
        if self.answer_arxiv_id and len(self.answer_arxiv_id) != len(self.answer):  # 有 arXiv 列表时必须可逐条配对。
            raise ValueError("answer_arxiv_id 非空时必须与 answer 长度一致")  # 防止错位身份映射污染金标。
        if any(not arxiv_id.strip() for arxiv_id in self.answer_arxiv_id):  # 显式提供的 arXiv 列表不得混入空白占位。
            raise ValueError("answer_arxiv_id 不能包含空白标识")  # 要求来源记录使用空列表表达整体缺失。
        return self  # 返回通过已确认 PaSa 格式边界校验的记录。
