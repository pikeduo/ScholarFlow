"""对用户本地 LongEval 2025 CORE abstract 数据执行零网络 Phase 0 审计。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from backend.app.models.paper_identity import normalize_doi
from evaluation.contracts.longeval import LongEvalAuditSummary, LongEvalQueryDoiEligibility, LongEvalSplit, LongEvalSplitAudit


DEFAULT_RAW_ROOT = Path("data") / "evaluation" / "longeval_2025" / "raw" / "extracted"
DEFAULT_OUTPUT_DIR = Path("data") / "evaluation" / "longeval_2025" / "reports" / "longeval-audit"
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", flags=re.IGNORECASE)


class LongEvalAuditError(RuntimeError):
    """表示本地文件布局、格式或输出边界不满足已确认 LongEval 约定。"""


@dataclass(frozen=True)
class _SplitSpec:
    split: LongEvalSplit
    queries_path: Path
    qrels_path: Path
    documents_directory: Path
    qrels_layout: str


def audit_longeval_dataset(*, raw_root: Path = DEFAULT_RAW_ROOT, output_dir: Path = DEFAULT_OUTPUT_DIR) -> LongEvalAuditSummary:
    """扫描全部本地 LongEval 数据并原子写入审计目录，不访问网络或模型。"""
    normalized_root = raw_root.expanduser().resolve()
    normalized_output = output_dir.expanduser().resolve()
    if normalized_output.exists():
        raise FileExistsError(f"LongEval 审计输出目录已存在：{normalized_output}")
    audits: list[LongEvalSplitAudit] = []
    eligibility: list[LongEvalQueryDoiEligibility] = []
    for spec in _build_split_specs(normalized_root):
        audit, records = _audit_split(spec, normalized_root)
        audits.append(audit)
        eligibility.extend(records)
    summary = LongEvalAuditSummary(
        raw_root=str(normalized_root),
        splits=audits,
        total_query_count=sum(item.query_count for item in audits),
        total_qrels_count=sum(item.qrels_count for item in audits),
        total_positive_judgment_count=sum(item.positive_judgment_count for item in audits),
        total_unique_gold_doi_count=sum(item.unique_gold_doi_count for item in audits),
        total_doi_eligible_query_count=sum(item.doi_eligible_query_count for item in audits),
        total_excluded_no_doi_gold_query_count=sum(item.excluded_no_doi_gold_query_count for item in audits),
        warnings=["qrels 来自 click model；未出现在 qrels 中不能解释为论文绝对不相关。", "本审计以 relevance > 0 作为 DOI Gold 候选规则，完整原始等级分布已保留。", "DOI 缺失或异常时不使用标题、作者、arXiv 或平台 ID 补齐。"],
    )
    _write_output(normalized_output, summary, eligibility)
    return summary


def _build_split_specs(root: Path) -> list[_SplitSpec]:
    train_root = root / "train" / "longeval_sci_training_2025_abstract"
    test_root = root / "test" / "longeval_sci_testing_2025_abstract"
    qrels_root = root / "test-qrels" / "longeval_sci_qrels"
    specs = [
        _SplitSpec("train", train_root / "queries.txt", train_root / "qrels.txt", train_root / "documents", "train"),
        _SplitSpec("heldout", test_root / "queries_2024-11_test.txt", qrels_root / "qrels-longeval-sci-2024-11-test", test_root / "documents", "trec"),
        _SplitSpec("future", test_root / "queries_2025-01_test.txt", qrels_root / "qrels-longeval-sci-2025-01", test_root / "documents", "trec"),
    ]
    for spec in specs:
        if not spec.queries_path.is_file() or not spec.qrels_path.is_file() or not spec.documents_directory.is_dir():
            raise LongEvalAuditError(f"LongEval {spec.split} 的已确认 queries、qrels 或 documents 路径缺失")
    return specs


def _audit_split(spec: _SplitSpec, raw_root: Path) -> tuple[LongEvalSplitAudit, list[LongEvalQueryDoiEligibility]]:
    digest = hashlib.sha256()
    query_ids = _read_queries(spec.queries_path, raw_root, digest)
    qrels, relevance_distribution = _read_qrels(spec, raw_root, digest, set(query_ids))
    positives_by_query: dict[str, set[str]] = defaultdict(set)
    positive_judgment_counts: Counter[str] = Counter()
    for query_id, document_id, relevance in qrels:
        if relevance > 0:
            positives_by_query[query_id].add(document_id)
            positive_judgment_counts[query_id] += 1
    positive_document_ids = set().union(*positives_by_query.values()) if positives_by_query else set()
    document_stats, doi_by_document = _scan_documents(spec.documents_directory, raw_root, digest, positive_document_ids)
    missing_document_ids = positive_document_ids - set(doi_by_document)
    records: list[LongEvalQueryDoiEligibility] = []
    relevant_with_doi = relevant_without_doi = invalid_relevant_doi = duplicate_relevant_doi = 0
    all_gold_dois: set[str] = set()
    for query_id in query_ids:
        query_dois: set[str] = set()
        document_ids = positives_by_query[query_id]
        for document_id in document_ids:
            status, doi = doi_by_document.get(document_id, ("missing_document", None))
            if doi is None:
                relevant_without_doi += 1
                invalid_relevant_doi += int(status == "invalid")
                continue
            relevant_with_doi += 1
            duplicate_relevant_doi += int(doi in query_dois)
            query_dois.add(doi)
            all_gold_dois.add(doi)
        records.append(LongEvalQueryDoiEligibility(query_id=query_id, split=spec.split, positive_judgment_count=positive_judgment_counts[query_id], positive_document_count=len(document_ids), gold_doi_count=len(query_dois), excluded_no_doi_gold=not query_dois))
    denominator = relevant_with_doi + relevant_without_doi
    warnings = []
    if missing_document_ids:
        warnings.append(f"存在 {len(missing_document_ids)} 个正相关 qrels document_id 未在 documents 中找到。")
    if document_stats["duplicate_relevant_document"]:
        warnings.append(f"documents 中存在 {document_stats['duplicate_relevant_document']} 条重复的正相关 document_id 记录；首条记录作为审计基准。")
    if document_stats["conflicting_relevant_document_doi"]:
        warnings.append(f"其中 {document_stats['conflicting_relevant_document_doi']} 条重复正相关记录的 DOI 状态或值冲突。")
    if document_stats["invalid_doi"]:
        warnings.append(f"documents 中存在 {document_stats['invalid_doi']} 条不可规范化 DOI。")
    return LongEvalSplitAudit(
        split=spec.split,
        query_count=len(query_ids),
        qrels_count=len(qrels),
        qrels_query_count=len({row[0] for row in qrels}),
        relevance_distribution=dict(sorted(relevance_distribution.items())),
        positive_judgment_count=sum(positive_judgment_counts.values()),
        unique_positive_document_count=len(positive_document_ids),
        matched_positive_document_count=len(positive_document_ids) - len(missing_document_ids),
        missing_positive_document_count=len(missing_document_ids),
        duplicate_relevant_document_record_count=document_stats["duplicate_relevant_document"],
        conflicting_relevant_document_doi_count=document_stats["conflicting_relevant_document_doi"],
        document_count=document_stats["documents"],
        documents_with_valid_doi=document_stats["valid_doi"],
        documents_without_doi=document_stats["missing_doi"],
        documents_with_invalid_doi=document_stats["invalid_doi"],
        relevant_documents_with_doi=relevant_with_doi,
        relevant_documents_without_doi=relevant_without_doi,
        invalid_relevant_doi_count=invalid_relevant_doi,
        duplicate_relevant_doi_count=duplicate_relevant_doi,
        unique_gold_doi_count=len(all_gold_dois),
        doi_gold_coverage=relevant_with_doi / denominator if denominator else 0.0,
        doi_eligible_query_count=sum(not item.excluded_no_doi_gold for item in records),
        excluded_no_doi_gold_query_count=sum(item.excluded_no_doi_gold for item in records),
        input_sha256=digest.hexdigest(),
        warnings=warnings,
    ), records


def _read_queries(path: Path, root: Path, digest: object) -> list[str]:
    query_ids: list[str] = []
    seen: set[str] = set()
    for line_number, raw in _hashed_lines(path, root, digest):
        line = _decode(raw, path, line_number).rstrip("\r\n")
        parts = line.split("\t", maxsplit=1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise LongEvalAuditError(f"queries 必须为 query_id<TAB>query：{path}:{line_number}")
        query_id = parts[0].strip()
        if query_id in seen:
            raise LongEvalAuditError(f"queries 存在重复 query_id：{query_id}")
        seen.add(query_id)
        query_ids.append(query_id)
    return query_ids


def _read_qrels(spec: _SplitSpec, root: Path, digest: object, known_query_ids: set[str]) -> tuple[list[tuple[str, str, int]], Counter[str]]:
    rows: list[tuple[str, str, int]] = []
    distribution: Counter[str] = Counter()
    for line_number, raw in _hashed_lines(spec.qrels_path, root, digest):
        fields = _decode(raw, spec.qrels_path, line_number).split()
        if len(fields) != 4:
            raise LongEvalAuditError(f"qrels 必须恰好四列：{spec.qrels_path}:{line_number}")
        if spec.qrels_layout == "train":
            query_id, snapshot, document_id, relevance_text = fields
            if not snapshot:
                raise LongEvalAuditError(f"Train qrels snapshot 不能为空：{spec.qrels_path}:{line_number}")
        else:
            query_id, iteration, document_id, relevance_text = fields
            if iteration != "0":
                raise LongEvalAuditError(f"TREC qrels 第二列必须为 0：{spec.qrels_path}:{line_number}")
        if query_id not in known_query_ids:
            raise LongEvalAuditError(f"qrels 引用了不存在的 query_id：{query_id}")
        try:
            relevance = int(relevance_text)
        except ValueError as exc:
            raise LongEvalAuditError(f"qrels relevance 必须为整数：{spec.qrels_path}:{line_number}") from exc
        rows.append((query_id, document_id, relevance))
        distribution[relevance_text] += 1
    return rows, distribution


def _scan_documents(directory: Path, root: Path, digest: object, relevant_ids: set[str]) -> tuple[dict[str, int], dict[str, tuple[str, str | None]]]:
    files = sorted(path for path in directory.glob("*.jsonl") if path.is_file())
    if not files:
        raise LongEvalAuditError(f"documents 目录不包含 JSONL 文件：{directory}")
    stats = {"documents": 0, "valid_doi": 0, "missing_doi": 0, "invalid_doi": 0, "duplicate_relevant_document": 0, "conflicting_relevant_document_doi": 0}
    doi_by_document: dict[str, tuple[str, str | None]] = {}
    for path in files:
        for line_number, raw in _hashed_lines(path, root, digest):
            try:
                record = json.loads(_decode(raw, path, line_number))
            except json.JSONDecodeError as exc:
                raise LongEvalAuditError(f"documents JSONL 解析失败：{path}:{line_number}") from exc
            if not isinstance(record, dict) or record.get("id") is None:
                raise LongEvalAuditError(f"documents 缺少 id：{path}:{line_number}")
            document_id = str(record["id"])
            raw_doi = record.get("doi")
            if raw_doi is None or (isinstance(raw_doi, str) and not raw_doi.strip()):
                status, normalized_doi = "missing", None
                stats["missing_doi"] += 1
            elif not isinstance(raw_doi, str) or (normalized_doi := _normalize_strict_doi(raw_doi)) is None:
                status, normalized_doi = "invalid", None
                stats["invalid_doi"] += 1
            else:
                status = "valid"
                stats["valid_doi"] += 1
            stats["documents"] += 1
            if document_id in relevant_ids:
                if document_id in doi_by_document:
                    stats["duplicate_relevant_document"] += 1
                    stats["conflicting_relevant_document_doi"] += int(doi_by_document[document_id] != (status, normalized_doi))
                    continue
                doi_by_document[document_id] = (status, normalized_doi)
    return stats, doi_by_document


def _hashed_lines(path: Path, root: Path, digest: object):
    try:
        relative_path = path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise LongEvalAuditError(f"输入文件不在 raw root 内：{path}") from exc
    digest.update(relative_path.encode("utf-8") + b"\0")
    with path.open("rb") as stream:
        for line_number, raw in enumerate(stream, start=1):
            digest.update(raw)
            yield line_number, raw


def _decode(raw: bytes, path: Path, line_number: int) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LongEvalAuditError(f"输入不是 UTF-8：{path}:{line_number}") from exc


def _normalize_strict_doi(value: str) -> str | None:
    """在生产展示规范化之上施加 DOI 语法门槛，避免任意文本进入 DOI Gold。"""
    normalized = normalize_doi(value)
    if normalized is None or _DOI_PATTERN.fullmatch(normalized) is None:
        return None
    return normalized


def _write_output(output_dir: Path, summary: LongEvalAuditSummary, eligibility: list[LongEvalQueryDoiEligibility]) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = _temporary_directory(output_dir.parent, output_dir.name)
    try:
        (temporary_dir / "audit.json").write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (temporary_dir / "query_doi_eligibility.jsonl").write_text("".join(item.model_dump_json() + "\n" for item in eligibility), encoding="utf-8")
        (temporary_dir / "audit.md").write_text(_markdown(summary), encoding="utf-8")
        os.replace(temporary_dir, output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def _temporary_directory(parent: Path, label: str) -> Path:
    for _ in range(10):
        candidate = parent / f".{label}.{uuid4().hex}.tmp"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise LongEvalAuditError("无法创建 LongEval 审计临时目录")


def _markdown(summary: LongEvalAuditSummary) -> str:
    lines = ["# LongEval 2025 CORE DOI 数据审计", "", f"- Query 总数：{summary.total_query_count}", f"- qrels 总数：{summary.total_qrels_count}", f"- DOI-eligible Query 总数：{summary.total_doi_eligible_query_count}", f"- excluded_no_doi_gold 总数：{summary.total_excluded_no_doi_gold_query_count}", "", "| Split | Query | qrels | Gold DOI | Eligible | Excluded | Coverage |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for item in summary.splits:
        lines.append(f"| {item.split} | {item.query_count} | {item.qrels_count} | {item.unique_gold_doi_count} | {item.doi_eligible_query_count} | {item.excluded_no_doi_gold_query_count} | {item.doi_gold_coverage:.2%} |")
    lines.extend(["", "## 解释边界", "", *[f"- {warning}" for warning in summary.warnings], ""])
    return "\n".join(lines)
