"""定义 LongEval 2025 CORE 本地数据审计的稳定输出契约。"""

from typing import Literal

from pydantic import BaseModel, Field


LongEvalSplit = Literal["train", "heldout", "future"]
LongEvalGoldEvidenceStatus = Literal["included", "missing_document", "missing_doi", "invalid_doi", "conflicting_doi"]


class LongEvalQueryDoiEligibility(BaseModel):
    """保存一条查询进入 DOI-strict Track 前的可复核资格统计。"""

    query_id: str = Field(min_length=1)
    split: LongEvalSplit
    positive_judgment_count: int = Field(ge=0)
    positive_document_count: int = Field(ge=0)
    gold_doi_count: int = Field(ge=0)
    excluded_no_doi_gold: bool


class LongEvalSplitAudit(BaseModel):
    """保存一个 LongEval split 的本地扫描统计。"""

    split: LongEvalSplit
    query_count: int = Field(ge=0)
    qrels_count: int = Field(ge=0)
    qrels_query_count: int = Field(ge=0)
    relevance_distribution: dict[str, int] = Field(default_factory=dict)
    positive_relevance_rule: str = "relevance > 0"
    positive_judgment_count: int = Field(ge=0)
    unique_positive_document_count: int = Field(ge=0)
    matched_positive_document_count: int = Field(ge=0)
    missing_positive_document_count: int = Field(ge=0)
    duplicate_relevant_document_record_count: int = Field(ge=0)
    conflicting_relevant_document_doi_count: int = Field(ge=0)
    document_count: int = Field(ge=0)
    documents_with_valid_doi: int = Field(ge=0)
    documents_without_doi: int = Field(ge=0)
    documents_with_invalid_doi: int = Field(ge=0)
    relevant_documents_with_doi: int = Field(ge=0)
    relevant_documents_without_doi: int = Field(ge=0)
    invalid_relevant_doi_count: int = Field(ge=0)
    duplicate_relevant_doi_count: int = Field(ge=0)
    unique_gold_doi_count: int = Field(ge=0)
    doi_gold_coverage: float = Field(ge=0.0, le=1.0)
    doi_eligible_query_count: int = Field(ge=0)
    excluded_no_doi_gold_query_count: int = Field(ge=0)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: list[str] = Field(default_factory=list)


class LongEvalAuditSummary(BaseModel):
    """保存全量 LongEval Phase 0 审计报告。"""

    schema_version: Literal["longeval-audit-v1"] = "longeval-audit-v1"
    raw_root: str = Field(min_length=1)
    positive_relevance_rule: str = "relevance > 0"
    splits: list[LongEvalSplitAudit] = Field(min_length=1)
    total_query_count: int = Field(ge=0)
    total_qrels_count: int = Field(ge=0)
    total_positive_judgment_count: int = Field(ge=0)
    total_unique_gold_doi_count: int = Field(ge=0)
    total_doi_eligible_query_count: int = Field(ge=0)
    total_excluded_no_doi_gold_query_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class LongEvalGoldEvidence(BaseModel):
    """保存一个正相关 qrels 判断进入或退出 DOI Gold 的可复核证据。"""

    query_id: str = Field(min_length=1)
    split: LongEvalSplit
    document_id: str = Field(min_length=1)
    relevance: int = Field(gt=0)
    status: LongEvalGoldEvidenceStatus
    normalized_doi: str | None = None


class LongEvalExcludedQuery(BaseModel):
    """保存没有可用 DOI Gold 的查询及其原始证据状态。"""

    query_id: str = Field(min_length=1)
    split: LongEvalSplit
    positive_judgment_count: int = Field(ge=0)
    positive_document_count: int = Field(ge=0)
    exclusion_reasons: list[LongEvalGoldEvidenceStatus] = Field(min_length=1)


class LongEvalGoldImportManifest(BaseModel):
    """冻结 LongEval DOI Gold 导入的审计输入、输出哈希和严格匹配规则。"""

    schema_version: Literal["longeval-doi-gold-import-v1"] = "longeval-doi-gold-import-v1"
    matching_policy: Literal["doi-strict-v1"] = "doi-strict-v1"
    positive_relevance_rule: Literal["relevance > 0"] = "relevance > 0"
    audit_schema_version: Literal["longeval-audit-v1"] = "longeval-audit-v1"
    audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_root: str = Field(min_length=1)
    source_input_sha256_by_split: dict[LongEvalSplit, str]
    gold_query_count_by_split: dict[LongEvalSplit, int]
    excluded_query_count_by_split: dict[LongEvalSplit, int]
    output_sha256: dict[str, str]
