"""将已审计的本地 LongEval 数据转换为 DOI-strict GoldQuery 与证据账本。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from evaluation.contracts.common import EvaluationPaper
from evaluation.contracts.gold import GoldQuery
from evaluation.contracts.longeval import LongEvalAuditSummary, LongEvalExcludedQuery, LongEvalGoldEvidence, LongEvalGoldEvidenceStatus, LongEvalGoldImportManifest, LongEvalSplit
from evaluation.runners.dataset_import import serialize_gold_queries
from evaluation.runners.longeval_audit import DEFAULT_OUTPUT_DIR as DEFAULT_AUDIT_OUTPUT_DIR
from evaluation.runners.longeval_audit import DEFAULT_RAW_ROOT, LongEvalAuditError, _build_split_specs, _decode, _hashed_lines, _normalize_strict_doi, _read_qrels, _read_queries


DEFAULT_OUTPUT_DIR = Path("data") / "evaluation" / "longeval_2025" / "processed" / "longeval-doi-gold"


class LongEvalGoldImportError(RuntimeError):
    """表示审计输入、原始数据或 DOI Gold 构建边界不一致。"""


def import_longeval_gold(*, raw_root: Path = DEFAULT_RAW_ROOT, audit_dir: Path = DEFAULT_AUDIT_OUTPUT_DIR, output_dir: Path = DEFAULT_OUTPUT_DIR) -> LongEvalGoldImportManifest:
    """读取已审计 LongEval raw，原子发布 DOI-strict Gold、evidence 与 excluded ledger。

    导入器重新计算 Phase 0 输入哈希，拒绝已在审计后发生变化的 raw 文件。它只使用
    `relevance > 0` 的 qrels；同一 document ID 的 DOI 状态或值出现冲突时，记录为
    `conflicting_doi`，且绝不选择任一记录作为 Gold。
    """
    normalized_raw_root = raw_root.expanduser().resolve()
    normalized_audit_dir = audit_dir.expanduser().resolve()
    normalized_output_dir = output_dir.expanduser().resolve()
    if normalized_output_dir.exists():
        raise FileExistsError(f"LongEval DOI Gold 输出目录已存在：{normalized_output_dir}")
    audit_path = normalized_audit_dir / "audit.json"
    if not audit_path.is_file():
        raise LongEvalGoldImportError(f"缺少已完成的 LongEval 审计报告：{audit_path}")
    audit_bytes = audit_path.read_bytes()
    try:
        audit = LongEvalAuditSummary.model_validate_json(audit_bytes)
    except ValueError as exc:
        raise LongEvalGoldImportError(f"LongEval 审计报告不符合 longeval-audit-v1：{audit_path}") from exc
    if Path(audit.raw_root).resolve() != normalized_raw_root:
        raise LongEvalGoldImportError("审计报告 raw_root 与本次导入 raw_root 不一致；请先重新执行 longeval-audit")
    audit_by_split = {item.split: item for item in audit.splits}
    if set(audit_by_split) != {"train", "heldout", "future"}:
        raise LongEvalGoldImportError("审计报告必须恰好包含 train、heldout、future 三个 split")

    all_gold: dict[LongEvalSplit, list[GoldQuery]] = {}
    all_evidence: list[LongEvalGoldEvidence] = []
    all_excluded: list[LongEvalExcludedQuery] = []
    source_hashes: dict[LongEvalSplit, str] = {}
    for spec in _build_split_specs(normalized_raw_root):
        try:
            gold, evidence, excluded, source_hash = _import_split(spec, normalized_raw_root)
        except LongEvalAuditError as exc:
            raise LongEvalGoldImportError(str(exc)) from exc
        if source_hash != audit_by_split[spec.split].input_sha256:
            raise LongEvalGoldImportError(f"{spec.split} 原始文件 SHA-256 与审计报告不一致；请先重新执行 longeval-audit")
        all_gold[spec.split] = gold
        all_evidence.extend(evidence)
        all_excluded.extend(excluded)
        source_hashes[spec.split] = source_hash

    output_files = _serialize_outputs(all_gold, all_evidence, all_excluded)
    manifest = LongEvalGoldImportManifest(
        audit_sha256=hashlib.sha256(audit_bytes).hexdigest(),
        raw_root=str(normalized_raw_root),
        source_input_sha256_by_split=source_hashes,
        gold_query_count_by_split={split: len(all_gold[split]) for split in ("train", "heldout", "future")},
        excluded_query_count_by_split={
            split: sum(item.split == split for item in all_excluded) for split in ("train", "heldout", "future")
        },
        output_sha256={name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, content in output_files.items()},
    )
    output_files["manifest.json"] = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_output_directory(normalized_output_dir, output_files)
    return manifest


def _import_split(spec: object, raw_root: Path) -> tuple[list[GoldQuery], list[LongEvalGoldEvidence], list[LongEvalExcludedQuery], str]:
    """解析一个 split 并返回 Gold、完整正相关证据、排除查询和审计兼容哈希。"""
    digest = hashlib.sha256()
    query_ids = _read_queries(spec.queries_path, raw_root, digest)
    qrels, _ = _read_qrels(spec, raw_root, digest, set(query_ids))
    positive_rows = [(query_id, document_id, relevance) for query_id, document_id, relevance in qrels if relevance > 0]
    positive_document_ids = {document_id for _, document_id, _ in positive_rows}
    states_by_document = _collect_document_states(spec.documents_directory, raw_root, digest, positive_document_ids)

    state_by_document = {document_id: _resolve_document_state(states) for document_id, states in states_by_document.items()}
    evidence: list[LongEvalGoldEvidence] = []
    doi_by_query: dict[str, list[str]] = defaultdict(list)
    seen_dois_by_query: dict[str, set[str]] = defaultdict(set)
    statuses_by_query: dict[str, set[LongEvalGoldEvidenceStatus]] = defaultdict(set)
    positive_document_ids_by_query: dict[str, set[str]] = defaultdict(set)
    positive_judgment_count_by_query: dict[str, int] = defaultdict(int)
    for query_id, document_id, relevance in positive_rows:
        status, doi = state_by_document.get(document_id, ("missing_document", None))
        evidence.append(LongEvalGoldEvidence(query_id=query_id, split=spec.split, document_id=document_id, relevance=relevance, status=status, normalized_doi=doi))
        statuses_by_query[query_id].add(status)
        positive_document_ids_by_query[query_id].add(document_id)
        positive_judgment_count_by_query[query_id] += 1
        if status == "included" and doi is not None and doi not in seen_dois_by_query[query_id]:
            seen_dois_by_query[query_id].add(doi)
            doi_by_query[query_id].append(doi)

    gold: list[GoldQuery] = []
    excluded: list[LongEvalExcludedQuery] = []
    for query_id in query_ids:
        query_text = _query_text_by_id(spec.queries_path, query_id)
        dois = doi_by_query[query_id]
        if not dois:
            excluded.append(
                LongEvalExcludedQuery(
                    query_id=query_id,
                    split=spec.split,
                    positive_judgment_count=positive_judgment_count_by_query[query_id],
                    positive_document_count=len(positive_document_ids_by_query[query_id]),
                    exclusion_reasons=sorted(statuses_by_query[query_id] or {"missing_doi"}),
                )
            )
            continue
        gold.append(
            GoldQuery(
                query_id=query_id,
                query=query_text,
                relevant_papers=[EvaluationPaper(doi=doi, source="longeval-2025-core") for doi in dois],
                metadata={"dataset": "longeval-2025-core", "split": spec.split, "matching_policy": "doi-strict-v1", "positive_relevance_rule": "relevance > 0"},
            )
        )
    return gold, evidence, excluded, digest.hexdigest()


def _query_text_by_id(path: Path, expected_query_id: str) -> str:
    """读取已验证 queries 文件中的单条文本，避免将 query 文本写入审计内存对象。"""
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line in stream:
            query_id, query_text = line.rstrip("\r\n").split("\t", maxsplit=1)
            if query_id == expected_query_id:
                return query_text
    raise LongEvalGoldImportError(f"queries 中缺少已解析 query_id：{expected_query_id}")


def _collect_document_states(directory: Path, raw_root: Path, digest: object, relevant_document_ids: set[str]) -> dict[str, set[tuple[LongEvalGoldEvidenceStatus, str | None]]]:
    """扫描已关联 documents，收集每个正相关 ID 的全部 DOI 状态而不任取首条。"""
    files = sorted(path for path in directory.glob("*.jsonl") if path.is_file())
    if not files:
        raise LongEvalGoldImportError(f"documents 目录不包含 JSONL 文件：{directory}")
    states_by_document: dict[str, set[tuple[LongEvalGoldEvidenceStatus, str | None]]] = defaultdict(set)
    for path in files:
        for line_number, raw in _hashed_lines(path, raw_root, digest):
            try:
                record = json.loads(_decode(raw, path, line_number))
            except json.JSONDecodeError as exc:
                raise LongEvalGoldImportError(f"documents JSONL 解析失败：{path}:{line_number}") from exc
            if not isinstance(record, dict) or record.get("id") is None:
                raise LongEvalGoldImportError(f"documents 缺少 id：{path}:{line_number}")
            document_id = str(record["id"])
            if document_id in relevant_document_ids:
                states_by_document[document_id].add(_classify_doi(record.get("doi")))
    return states_by_document


def _classify_doi(raw_doi: object) -> tuple[LongEvalGoldEvidenceStatus, str | None]:
    """只依据已保存的 DOI 字段分类，不补全或在线核验。"""
    if raw_doi is None or (isinstance(raw_doi, str) and not raw_doi.strip()):
        return "missing_doi", None
    if not isinstance(raw_doi, str) or (normalized_doi := _normalize_strict_doi(raw_doi)) is None:
        return "invalid_doi", None
    return "included", normalized_doi


def _resolve_document_state(states: set[tuple[LongEvalGoldEvidenceStatus, str | None]]) -> tuple[LongEvalGoldEvidenceStatus, str | None]:
    """将同一 document ID 的全部记录归约为严格、无猜测的 Gold 状态。"""
    if len(states) != 1:
        return "conflicting_doi", None
    return next(iter(states))


def _serialize_outputs(gold: dict[LongEvalSplit, list[GoldQuery]], evidence: list[LongEvalGoldEvidence], excluded: list[LongEvalExcludedQuery]) -> dict[str, str]:
    """稳定序列化所有导入产物，以便内容哈希和原子发布共用同一字节。"""
    outputs = {f"gold.{split}.jsonl": serialize_gold_queries(gold[split]) for split in ("train", "heldout", "future")}
    outputs["evidence.jsonl"] = "".join(item.model_dump_json() + "\n" for item in evidence)
    outputs["excluded.jsonl"] = "".join(item.model_dump_json() + "\n" for item in excluded)
    return outputs


def _write_output_directory(output_dir: Path, outputs: dict[str, str]) -> None:
    """将完整的导入目录写入同级临时目录后一次发布，禁止覆盖历史产物。"""
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = _temporary_directory(output_dir.parent, output_dir.name)
    try:
        for filename, content in outputs.items():
            (temporary_dir / filename).write_text(content, encoding="utf-8", newline="\n")
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
    raise LongEvalGoldImportError("无法创建 LongEval DOI Gold 临时目录")
