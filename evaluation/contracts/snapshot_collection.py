"""定义候选快照集合的审计 manifest 契约。"""

from datetime import datetime  # 记录集合完成封存的明确时区时间。
from typing import Literal  # 固定当前集合 manifest 的版本与选择策略。

from pydantic import BaseModel, ConfigDict, Field, model_validator  # 校验集合输入顺序、映射与哈希边界。


class CandidateSnapshotCollectionManifest(BaseModel):
    """记录多份单查询候选快照被组装为同一离线评测输入的完整审计信息。"""

    model_config = ConfigDict(extra="forbid")  # 拒绝未声明字段绕过集合审计。

    schema_version: Literal["candidate-snapshot-collection-manifest-v1"] = "candidate-snapshot-collection-manifest-v1"  # 固定当前集合 manifest 版本。
    selection_strategy: Literal["manifest-order-with-explicit-overrides-v1"] = "manifest-order-with-explicit-overrides-v1"  # 固定按 QueryIntent manifest 顺序与显式重试选择的策略。
    collection_id: str = Field(min_length=1)  # 保存用户冻结的评测候选集合标识。
    query_intent_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 关联已生成 QueryIntent manifest 的原始字节哈希。
    source_recall_count: int = Field(ge=1, le=100)  # 固定全部快照共享的在线来源召回上限。
    target_paper_count: int = Field(ge=1, le=100)  # 固定全部快照生成时的最终目标数量。
    query_id_order: list[str] = Field(min_length=1)  # 保存供离线评分和报告复用的稳定查询顺序。
    snapshot_directory: str = Field(min_length=1)  # 保存单查询快照文件所在的用户显式目录。
    selected_snapshot_paths: dict[str, str] = Field(default_factory=dict)  # 保存 query_id 到目录内相对快照路径的映射。
    selected_snapshot_ids: dict[str, str] = Field(default_factory=dict)  # 保存 query_id 到已封存 snapshot_id 的映射。
    selected_snapshot_hashes: dict[str, str] = Field(default_factory=dict)  # 保存 query_id 到已验证内容哈希的映射。
    ranking_candidate_counts: dict[str, int] = Field(default_factory=dict)  # 保存每条查询实际进入本地排序的候选数量。
    created_at: datetime  # 保存集合 manifest 的明确时区创建时间。

    @model_validator(mode="after")
    def validate_collection_mappings(self) -> "CandidateSnapshotCollectionManifest":
        """要求每个稳定查询恰好选择一份快照并保持完整映射。"""
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:  # 集合创建时间必须可跨机器复核。
            raise ValueError("created_at 必须包含明确时区")  # 拒绝本地模糊时间。
        normalized_query_ids = [query_id.strip() for query_id in self.query_id_order]  # 忽略无语义首尾空白后检查稳定键。
        if any(not query_id for query_id in normalized_query_ids):  # 空白查询标识无法关联金标、快照和报告。
            raise ValueError("query_id_order 不能包含空白 query_id")  # 返回可操作的集合边界错误。
        if normalized_query_ids != self.query_id_order:  # manifest 不能对同一 query_id 保留多个文本表示。
            raise ValueError("query_id_order 不能包含前后空白")  # 防止字典键与顺序数组漂移。
        if len(set(self.query_id_order)) != len(self.query_id_order):  # 同一查询只能对应一份共享在线快照。
            raise ValueError("query_id_order 不得包含重复 query_id")  # 拒绝重复评测分母。
        expected_query_ids = set(self.query_id_order)  # 建立所有映射必须覆盖的稳定键集合。
        if set(self.selected_snapshot_paths) != expected_query_ids:  # 文件选择必须完整覆盖冻结顺序。
            raise ValueError("selected_snapshot_paths 必须完整覆盖 query_id_order")  # 防止缺失或混入额外快照。
        if set(self.selected_snapshot_ids) != expected_query_ids:  # 快照标识必须与每条查询一一对应。
            raise ValueError("selected_snapshot_ids 必须完整覆盖 query_id_order")  # 阻止后续任务归档歧义。
        if set(self.selected_snapshot_hashes) != expected_query_ids:  # 每份快照都必须有可复核内容哈希。
            raise ValueError("selected_snapshot_hashes 必须完整覆盖 query_id_order")  # 阻止未封存输入进入离线排序。
        if set(self.ranking_candidate_counts) != expected_query_ids:  # 每条查询的候选不足必须被显式记录。
            raise ValueError("ranking_candidate_counts 必须完整覆盖 query_id_order")  # 防止报告用零或默认值伪造候选规模。
        if len(set(self.selected_snapshot_ids.values())) != len(self.selected_snapshot_ids):  # 不同查询不能复用同一个单查询快照。
            raise ValueError("selected_snapshot_ids 不得重复")  # 防止集合内重复在线输入。
        if any(not path.strip() for path in self.selected_snapshot_paths.values()):  # 空文件路径无法在后续审计中定位来源。
            raise ValueError("selected_snapshot_paths 不能包含空白路径")  # 拒绝不可追踪映射。
        if any(not snapshot_id.strip() for snapshot_id in self.selected_snapshot_ids.values()):  # 空快照标识无法关联任务计划。
            raise ValueError("selected_snapshot_ids 不能包含空白标识")  # 拒绝不完整归档键。
        if any(hash_value is None or len(hash_value) != 64 or any(character not in "0123456789abcdef" for character in hash_value) for hash_value in self.selected_snapshot_hashes.values()):  # 每个哈希必须保持 SHA-256 小写十六进制格式。
            raise ValueError("selected_snapshot_hashes 必须只包含小写 SHA-256")  # 防止未校验值混入 manifest。
        if any(isinstance(count, bool) or count < 0 for count in self.ranking_candidate_counts.values()):  # 允许真实来源成功但空候选，禁止伪造负数或布尔值。
            raise ValueError("ranking_candidate_counts 必须只包含非负整数")  # 保持候选不足可被报告而不是被拒绝。
        return self  # 返回通过全部集合边界校验的 manifest。
