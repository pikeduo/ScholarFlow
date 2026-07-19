"""定义完全离线开发集子集封存的可复核 manifest 契约。"""

from typing import Literal  # 限制当前明确声明的确定性选择策略。

from pydantic import BaseModel, ConfigDict, Field, model_validator  # 校验 manifest 的可复现性与哈希边界。


class GoldSubsetManifest(BaseModel):
    """保存从完整 GoldQuery 文件确定性选择开发集子集所需的全部审计信息。

    参数：
        selection_id：人工冻结的子集用途与版本标识。
        selection_seed：参与稳定哈希排序的显式种子。
        source_gold_sha256：输入 GoldQuery 文件原始 UTF-8 字节的 SHA-256。
        selected_gold_sha256：输出 GoldQuery JSONL 规范化字节的 SHA-256。
        selected_query_ids：按稳定选择排名排列的完整查询标识列表。
    """

    model_config = ConfigDict(extra="forbid")  # manifest 字段变化必须显式演进，禁止静默写入未审计信息。

    schema_version: Literal["gold-subset-manifest-v1"] = "gold-subset-manifest-v1"  # 冻结当前 manifest 数据契约版本。
    selection_strategy: Literal["sha256-query-id-v1"] = "sha256-query-id-v1"  # 冻结仅依赖显式输入和 query_id 的确定性选择算法。
    selection_id: str = Field(min_length=1)  # 保存用户明确指定的开发集子集标识。
    selection_seed: str = Field(min_length=1)  # 保存用户明确指定且参与排序的种子文本。
    source_gold_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 保存完整输入文件的不可变内容哈希。
    source_query_count: int = Field(ge=1)  # 保存输入 GoldQuery 的总查询数。
    selected_query_count: int = Field(ge=1)  # 保存被选择的开发集查询数。
    selected_gold_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 保存输出 JSONL 规范化内容哈希。
    selected_query_ids: list[str] = Field(min_length=1)  # 保存可独立复核和重新生成的稳定查询标识列表。

    @model_validator(mode="after")
    def validate_subset_boundary(self) -> "GoldSubsetManifest":
        """校验选择数量、标识唯一性和审计文本不会产生歧义。"""
        if not self.selection_id.strip():  # 空白子集标识无法区分不同实验用途。
            raise ValueError("selection_id 不能只包含空白")  # 在发布 manifest 前拒绝不可审计标签。
        if not self.selection_seed.strip():  # 空白种子会伪装成未明确选择规则。
            raise ValueError("selection_seed 不能只包含空白")  # 要求用户显式冻结种子文本。
        if self.selected_query_count > self.source_query_count:  # 子集不可能超过完整输入查询数。
            raise ValueError("selected_query_count 不能大于 source_query_count")  # 防止伪造或截断的 manifest。
        if len(self.selected_query_ids) != self.selected_query_count:  # 清单必须完整列出每一个被选择的查询。
            raise ValueError("selected_query_ids 长度必须等于 selected_query_count")  # 保证可复现性不依赖隐式随机状态。
        normalized_query_ids = [query_id.strip() for query_id in self.selected_query_ids]  # 去除无语义边界空白后检查标识。
        if any(not query_id for query_id in normalized_query_ids):  # 空白查询标识无法关联 GoldQuery 或候选快照。
            raise ValueError("selected_query_ids 不能包含空白标识")  # 保护后续快照和评分关联键。
        if len(set(normalized_query_ids)) != len(normalized_query_ids):  # 同一子集不得重复计入一个评测查询。
            raise ValueError("selected_query_ids 不能包含重复标识")  # 固定评测分母并避免多次来源调用。
        return self  # 返回通过全部可复核边界校验的 manifest。
