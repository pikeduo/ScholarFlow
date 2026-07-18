"""定义排序前候选快照、完整性哈希和阶段统计契约。"""

import hashlib  # 计算不可变候选快照的 SHA-256。
import json  # 生成跨运行稳定的规范化 JSON。
from datetime import datetime  # 保存快照生成时间并校验时区。
from typing import Literal  # 限制快照只能声明排序前流水线阶段。

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator  # 校验候选快照结构和数量关系。

from evaluation.contracts.common import EvaluationPaper, EvaluationUsage  # 复用论文身份与在线用量契约。


class CandidateSourceRecord(BaseModel):
    """保存候选论文在单个学术来源中的可复核命中信息。"""

    model_config = ConfigDict(extra="forbid")  # 拒绝未进入哈希契约的未知字段。

    source: str = Field(min_length=1)  # 保存来源名称。
    external_id: str = Field(min_length=1)  # 保存来源内稳定论文标识。
    raw_rank: int | None = Field(default=None, ge=1)  # 保存来源原始排名。
    matched_subqueries: list[str] = Field(default_factory=list)  # 保存命中的英文子查询文本。


class CandidatePaper(EvaluationPaper):
    """保存规范化、去重且已完成 RRF 的排序前论文候选。"""

    model_config = ConfigDict(extra="forbid")  # 防止候选元数据被静默丢弃。

    paper_id: str = Field(min_length=1)  # 快照内必须有稳定唯一标识。
    title: str = Field(min_length=1)  # 本地排序至少需要可展示标题。
    source: str = Field(min_length=1)  # 保存规范化记录的主来源。
    abstract: str = ""  # 保存本地模型可使用的公开摘要。
    keywords: list[str] = Field(default_factory=list)  # 保存来源关键词或规范化关键词。
    source_records: list[CandidateSourceRecord] = Field(default_factory=list)  # 保存多来源溯源与原始排名。
    rrf_score: float = Field(ge=0.0)  # 保存本地排序前的确定性融合分数。
    snapshot_rank: int = Field(ge=1)  # 保存 RRF 候选顺序中的一基排名。


class CandidateSnapshot(BaseModel):
    """保存一次在线候选生成后可被所有离线排序配置复用的不可变输入。"""

    model_config = ConfigDict(extra="forbid")  # 快照完整性校验必须覆盖全部输入字段。

    schema_version: Literal["1.0"] = "1.0"  # 固定第二阶段快照契约版本。
    snapshot_stage: Literal["normalized_deduplicated_rrf"] = "normalized_deduplicated_rrf"  # 明确禁止把最终结果冒充排序前快照。
    snapshot_id: str = Field(min_length=1)  # 保存候选快照唯一标识。
    snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")  # 保存不含自身字段的规范化内容哈希。
    query_id: str = Field(min_length=1)  # 关联数据集查询标识。
    run_id: str | None = None  # 可选关联生产搜索运行，但不读取其 SQLite 最终结果。
    query: str = Field(min_length=1)  # 保存用于离线排序的查询文本。
    query_intent: dict[str, object] = Field(default_factory=dict)  # 保存生成候选时冻结的结构化查询意图。
    source_recall_count: int = Field(ge=1, le=100)  # 保存每来源每轮召回上限。
    target_paper_count: int = Field(ge=1, le=100)  # 保存在线运行期望的最终论文数量。
    sources_used: list[str] = Field(min_length=1)  # 保存实际参与候选生成的学术来源。
    raw_candidate_count: int = Field(ge=0)  # 保存来源返回记录总数。
    normalized_candidate_count: int = Field(ge=0)  # 保存规范化后、身份去重前数量。
    deduplicated_candidate_count: int = Field(ge=0)  # 保存去重并完成 RRF 后数量。
    papers: list[CandidatePaper] = Field(default_factory=list)  # 保存严格按 RRF 顺序排列的候选。
    usage: EvaluationUsage = Field(default_factory=EvaluationUsage)  # 保存在线候选生成阶段已冻结用量。
    stop_reason: str | None = None  # 保存在线候选生成停止原因。
    warnings: list[str] = Field(default_factory=list)  # 保存来源降级或字段缺失警告。
    created_at: datetime  # 保存带时区的快照生成时间。

    @field_validator("query_intent")
    @classmethod
    def validate_query_intent_json(cls, value: dict[str, object]) -> dict[str, object]:
        """要求 QueryIntent 快照可以稳定写入 JSON。"""
        try:  # 只接受可由标准 JSON 表达的结构。
            json.dumps(value, ensure_ascii=False, sort_keys=True)  # 验证嵌套值不含模型、路径或其他对象。
        except (TypeError, ValueError) as exc:  # 将不可序列化输入映射为契约错误。
            raise ValueError("query_intent 必须是可序列化 JSON 对象") from exc  # 阻止不稳定对象进入快照哈希。
        return value  # 返回通过校验的查询意图。

    @model_validator(mode="after")
    def validate_snapshot_boundaries(self) -> "CandidateSnapshot":
        """校验候选数量、排名、RRF 顺序、来源和时间边界。"""
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:  # 快照时间必须可跨机器复核。
            raise ValueError("created_at 必须包含明确时区")  # 拒绝本地模糊时间。
        if len(set(self.sources_used)) != len(self.sources_used):  # 来源列表不得重复扩大来源计数。
            raise ValueError("sources_used 不得包含重复来源")  # 返回稳定数据错误。
        if not self.raw_candidate_count >= self.normalized_candidate_count >= self.deduplicated_candidate_count:  # 各阶段数量只能递减或保持。
            raise ValueError("候选数量必须满足 raw >= normalized >= deduplicated")  # 防止流水线阶段统计倒置。
        if self.deduplicated_candidate_count != len(self.papers):  # 去重后数量必须与实际列表一致。
            raise ValueError("deduplicated_candidate_count 必须等于 papers 数量")  # 防止报告与候选内容漂移。
        expected_ranks = list(range(1, len(self.papers) + 1))  # 构造连续一基 RRF 排名。
        if [paper.snapshot_rank for paper in self.papers] != expected_ranks:  # 快照顺序与显式排名必须一致。
            raise ValueError("snapshot_rank 必须按 papers 顺序从 1 连续递增")  # 拒绝断裂或重复排名。
        if any(left.rrf_score < right.rrf_score for left, right in zip(self.papers, self.papers[1:])):  # RRF 分数不得逆序。
            raise ValueError("papers 必须按 rrf_score 降序保存")  # 保证关闭本地模型时基线确定。
        available_sources = set(self.sources_used)  # 建立来源覆盖集合。
        for paper in self.papers:  # 校验每篇论文的来源溯源。
            paper_sources = {paper.source, *(record.source for record in paper.source_records)}  # 汇总主来源和多源记录。
            if not paper_sources.issubset(available_sources):  # 快照不得引用未声明来源。
                raise ValueError(f"论文 {paper.paper_id} 引用了 sources_used 之外的来源")  # 返回可定位错误。
        return self  # 返回通过结构校验的快照。


def compute_snapshot_hash(snapshot: CandidateSnapshot) -> str:
    """计算不含 ``snapshot_hash`` 自身的规范化 SHA-256。"""
    payload = snapshot.model_dump(mode="json", exclude={"snapshot_hash"})  # 排除自引用字段并转换日期等 JSON 类型。
    canonical_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))  # 固定键顺序和空白规则。
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()  # 返回小写十六进制摘要。


def seal_snapshot(snapshot: CandidateSnapshot) -> CandidateSnapshot:
    """返回写入当前内容哈希的不可变快照副本。"""
    return snapshot.model_copy(update={"snapshot_hash": compute_snapshot_hash(snapshot)})  # 不修改调用方持有的原对象。
