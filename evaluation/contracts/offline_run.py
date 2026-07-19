"""定义离线排序实际执行结果的不可覆盖归档 manifest。"""

from datetime import datetime  # 记录带时区的本地执行完成时间。
from typing import Literal  # 固定 manifest 当前格式版本。

from pydantic import BaseModel, ConfigDict, Field, model_validator  # 校验执行输入、输出与任务映射。


class OfflineRankingRunManifest(BaseModel):
    """审计一次由用户显式授权的离线排序执行，不包含模型目录或查询正文。"""

    model_config = ConfigDict(extra="forbid")  # 禁止未声明字段绕过结果归档审计。

    schema_version: Literal["offline-ranking-run-manifest-v1"] = "offline-ranking-run-manifest-v1"  # 固定归档契约版本。
    run_id: str = Field(min_length=1)  # 保存用户冻结的本次执行标识。
    matrix_id: str = Field(min_length=1)  # 关联已审核的消融矩阵。
    matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 冻结矩阵原始字节哈希。
    ablation_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 冻结用户先前生成的计划原始字节哈希。
    snapshots_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 冻结集合快照 JSONL 原始字节哈希。
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 冻结新结果 JSONL 原始字节哈希。
    selected_experiment_ids: list[str] = Field(min_length=1)  # 保存本次实际执行的矩阵子集。
    snapshot_ids: list[str] = Field(min_length=1)  # 保存本次逐条读取的快照顺序。
    snapshot_hashes: dict[str, str] = Field(default_factory=dict)  # 保存每份快照的内容哈希。
    task_count: int = Field(ge=1)  # 保存快照数乘实验数的实际任务数量。
    local_model_stages: list[str] = Field(default_factory=list)  # 保存实际执行的本地模型阶段，不记录机器路径。
    created_at: datetime  # 保存明确时区的本地归档时间。

    @model_validator(mode="after")
    def validate_execution_mapping(self) -> "OfflineRankingRunManifest":
        """确认任务数量、快照映射与时间可被独立复核。"""
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:  # 时间必须可跨机器比较。
            raise ValueError("created_at 必须包含明确时区")  # 拒绝本地模糊时间。
        if len(set(self.selected_experiment_ids)) != len(self.selected_experiment_ids):  # 重复实验会令结果分母歧义。
            raise ValueError("selected_experiment_ids 不得重复")  # 保护横向比较边界。
        if len(set(self.snapshot_ids)) != len(self.snapshot_ids):  # 同一在线快照不能在一次执行中重复。
            raise ValueError("snapshot_ids 不得重复")  # 防止重复归档。
        if set(self.snapshot_hashes) != set(self.snapshot_ids):  # 每份输入都必须有内容哈希。
            raise ValueError("snapshot_hashes 必须完整覆盖 snapshot_ids")  # 阻止未封存快照进入结果。
        if self.task_count != len(self.selected_experiment_ids) * len(self.snapshot_ids):  # 固定实际任务规模而非猜测数量。
            raise ValueError("task_count 必须等于实验数乘快照数")  # 防止部分任务被静默遗漏。
        return self  # 返回通过归档边界校验的 manifest。
