"""定义候选覆盖与论文身份可比性的完全离线诊断契约。"""

from pydantic import BaseModel, Field  # 约束每条诊断记录均可稳定写入 JSON。


class QueryCoverageDiagnostic(BaseModel):
    """保存一条金标查询与同条候选快照之间的身份覆盖审计结果。"""

    query_id: str = Field(min_length=1)  # 保存唯一查询标识，不复制查询正文。
    snapshot_id: str = Field(min_length=1)  # 保存参与比较的不可变快照标识。
    gold_paper_count: int = Field(ge=0)  # 保存该查询的去重后金标论文数量。
    candidate_paper_count: int = Field(ge=0)  # 保存进入排序的共享候选数量。
    matched_gold_paper_count: int = Field(ge=0)  # 保存至少命中一个候选的金标论文数。
    matched_candidate_paper_count: int = Field(ge=0)  # 保存至少命中一个金标的候选论文数。
    strong_identifier_gold_count: int = Field(ge=0)  # 保存具有强标识符的金标论文数量。
    strong_identifier_candidate_count: int = Field(ge=0)  # 保存具有强标识符的候选论文数量。
    strong_identifier_match_count: int = Field(ge=0)  # 保存由相同强标识符确认的论文对数量。
    internal_identifier_match_count: int = Field(ge=0)  # 保存仅由内部标识符确认的论文对数量。
    title_fallback_match_count: int = Field(ge=0)  # 保存由标题、年份和作者回退规则确认的论文对数量。
    diagnostic_flags: list[str] = Field(default_factory=list)  # 保存事实性诊断标记，不推断在线来源或语义模型原因。


class CoverageDiagnosticSummary(BaseModel):
    """保存整个共享候选集合的零命中覆盖诊断汇总。"""

    schema_version: str = "coverage-diagnostic-v1"  # 固定机器可读报告结构版本。
    matching_rule: str = "papers_match-v1"  # 冻结与正式检索指标完全相同的论文匹配规则。
    gold_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 保存金标原始字节摘要。
    snapshots_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 保存候选集合原始字节摘要。
    query_count: int = Field(ge=1)  # 保存已严格对齐的查询数量。
    total_gold_paper_count: int = Field(ge=0)  # 保存全体金标论文数量。
    total_candidate_paper_count: int = Field(ge=0)  # 保存全体排序输入候选数量。
    matched_gold_paper_count: int = Field(ge=0)  # 保存全体至少一次命中的金标论文数量。
    zero_match_query_count: int = Field(ge=0)  # 保存没有任何金标论文命中的查询数量。
    strong_identifier_gold_count: int = Field(ge=0)  # 保存全体具强标识符金标论文数量。
    strong_identifier_candidate_count: int = Field(ge=0)  # 保存全体具强标识符候选论文数量。
    warnings: list[str] = Field(default_factory=list)  # 保存对报告解释边界的固定提示。
