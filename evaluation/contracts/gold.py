"""定义公开数据集和人工 fixture 共用的金标查询契约。"""

from pydantic import BaseModel, Field  # 提供 JSONL 金标字段校验。

from evaluation.contracts.common import EvaluationPaper, JsonScalar  # 复用论文身份和 JSON 元数据边界。


class GoldQuery(BaseModel):
    """保存一条查询及其人工或数据集提供的相关论文集合。"""

    query_id: str = Field(min_length=1)  # 保存数据集内稳定查询标识。
    query: str = Field(min_length=1)  # 保存用于人工审阅的原始查询。
    relevant_papers: list[EvaluationPaper] = Field(default_factory=list)  # 保存可为空的金标相关论文集合。
    metadata: dict[str, JsonScalar] = Field(default_factory=dict)  # 保存数据集、split 等可复核标量元数据。
