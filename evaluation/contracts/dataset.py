"""定义用户本地准备的公开评测数据集金标输入契约。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator  # 提供严格 JSONL 字段和保留元数据校验。

from evaluation.contracts.common import EvaluationPaper, JsonScalar  # 复用论文身份与可安全归档的标量元数据边界。


class PreparedDatasetGoldRecord(BaseModel):
    """保存一条经用户准备、但尚未分配 ScholarFlow 金标标识的数据集查询。

    参数：
        source_query_id：原始数据集内稳定查询标识。
        query：数据集提供或人工确认的查询文本。
        relevant_papers：人工或公开数据集标注的相关论文。
        metadata：仅包含可直接写入 JSON 的来源附加标量。
    """

    model_config = ConfigDict(extra="forbid")  # 拒绝未经文档声明的第三方字段，避免静默误映射。

    source_query_id: str = Field(min_length=1)  # 保存原始数据集查询键而不重写其语义。
    query: str = Field(min_length=1)  # 保存可人工审阅的原始查询文本。
    relevant_papers: list[EvaluationPaper] = Field(default_factory=list)  # 保存可为空但必须结构有效的金标论文集合。
    metadata: dict[str, JsonScalar] = Field(default_factory=dict)  # 保存许可证、原始 split 说明等可复核标量。

    @model_validator(mode="after")
    def validate_metadata_boundary(self) -> "PreparedDatasetGoldRecord":
        """校验查询文本和来源元数据不会破坏稳定归档边界。"""
        if not self.source_query_id.strip():  # 仅空白的来源查询键无法形成稳定命名空间标识。
            raise ValueError("source_query_id 不能只包含空白")  # 在写入输出前拒绝不可追溯记录。
        if not self.query.strip():  # 仅空白的查询无法作为人工可审阅评测输入。
            raise ValueError("query 不能只包含空白")  # 不让 Pydantic 的最小长度校验遗漏空白边界。
        reserved_keys = {"dataset", "split", "source_query_id", "import_schema_version"}  # 固定由导入器生成的可追溯字段。
        conflicting_keys = sorted(reserved_keys & set(self.metadata))  # 找出会造成归档含义歧义的来源字段。
        if conflicting_keys:  # 来源数据不得伪装成导入器生成的审计信息。
            raise ValueError(f"metadata 不能包含保留字段: {', '.join(conflicting_keys)}")  # 返回可定位的准备数据错误。
        return self  # 返回通过数据归档边界校验的记录。
