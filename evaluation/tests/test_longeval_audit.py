"""测试 LongEval 本地数据审计的格式、DOI 边界与原子输出。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.runners.longeval_audit import LongEvalAuditError, audit_longeval_dataset


def _write_fixture(root: Path) -> None:
    """写入已确认文件布局的最小零网络 LongEval fixture。"""
    train_root = root / "train" / "longeval_sci_training_2025_abstract"
    test_root = root / "test" / "longeval_sci_testing_2025_abstract"
    qrels_root = root / "test-qrels" / "longeval_sci_qrels"
    for directory in (train_root / "documents", test_root / "documents", qrels_root):
        directory.mkdir(parents=True, exist_ok=True)
    (train_root / "queries.txt").write_text("train-q1\ttrain query\n", encoding="utf-8")
    (train_root / "qrels.txt").write_text("train-q1 2024-11 t1 2\ntrain-q1 2024-11 t2 0\n", encoding="utf-8")
    (test_root / "queries_2024-11_test.txt").write_text("heldout-q1\theldout query\n", encoding="utf-8")
    (test_root / "queries_2025-01_test.txt").write_text("future-q1\tfuture query\n", encoding="utf-8")
    (qrels_root / "qrels-longeval-sci-2024-11-test").write_text("heldout-q1 0 s1 1\n", encoding="utf-8")
    (qrels_root / "qrels-longeval-sci-2025-01").write_text("future-q1 0 s2 2\n", encoding="utf-8")
    (train_root / "documents" / "documents_000001.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {"id": "t1", "doi": "https://doi.org/10.1000/Train"},
                {"id": "t2", "doi": None},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (test_root / "documents" / "documents_000001.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {"id": "s1", "doi": "not-a-doi"},
                {"id": "s2", "doi": "10.2000/future"},
                {"id": "s2", "doi": None},
            )
        )
        + "\n",
        encoding="utf-8",
    )


def test_audit_writes_doi_eligibility_and_preserves_missing_or_invalid_doi(tmp_path: Path) -> None:
    """审计应以 DOI 为唯一金标身份，不对缺失或异常 DOI 补齐。"""
    raw_root = tmp_path / "raw"
    output_dir = tmp_path / "report"
    _write_fixture(raw_root)

    summary = audit_longeval_dataset(raw_root=raw_root, output_dir=output_dir)

    assert summary.total_query_count == 3
    assert summary.total_doi_eligible_query_count == 2
    assert summary.total_excluded_no_doi_gold_query_count == 1
    heldout = next(item for item in summary.splits if item.split == "heldout")
    assert heldout.documents_with_invalid_doi == 1
    assert heldout.excluded_no_doi_gold_query_count == 1
    assert heldout.duplicate_relevant_document_record_count == 0
    future = next(item for item in summary.splits if item.split == "future")
    assert future.duplicate_relevant_document_record_count == 1
    assert future.conflicting_relevant_document_doi_count == 1
    assert (output_dir / "audit.json").is_file()
    records = [json.loads(line) for line in (output_dir / "query_doi_eligibility.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records == [
        {"query_id": "train-q1", "split": "train", "positive_judgment_count": 1, "positive_document_count": 1, "gold_doi_count": 1, "excluded_no_doi_gold": False},
        {"query_id": "heldout-q1", "split": "heldout", "positive_judgment_count": 1, "positive_document_count": 1, "gold_doi_count": 0, "excluded_no_doi_gold": True},
        {"query_id": "future-q1", "split": "future", "positive_judgment_count": 1, "positive_document_count": 1, "gold_doi_count": 1, "excluded_no_doi_gold": False},
    ]


def test_audit_rejects_unknown_qrels_query_and_existing_output(tmp_path: Path) -> None:
    """qrels 不得扩展查询集合，且已有报告不得被新的审计覆盖。"""
    raw_root = tmp_path / "raw"
    _write_fixture(raw_root)
    train_qrels = raw_root / "train" / "longeval_sci_training_2025_abstract" / "qrels.txt"
    train_qrels.write_text("unknown 2024-11 t1 2\n", encoding="utf-8")
    with pytest.raises(LongEvalAuditError, match="不存在的 query_id"):
        audit_longeval_dataset(raw_root=raw_root, output_dir=tmp_path / "report")

    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    with pytest.raises(FileExistsError, match="输出目录已存在"):
        audit_longeval_dataset(raw_root=raw_root, output_dir=output_dir)
