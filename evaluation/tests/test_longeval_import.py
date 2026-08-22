"""测试 LongEval DOI Gold 导入仅使用本地已审计事实。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.cli import main
from evaluation.contracts.gold import GoldQuery
from evaluation.runners.fixture import load_jsonl
from evaluation.runners.longeval_audit import audit_longeval_dataset
from evaluation.runners.longeval_import import LongEvalGoldImportError, import_longeval_gold
from evaluation.tests.test_longeval_audit import _write_fixture


def test_import_builds_doi_gold_and_excludes_conflicting_documents(tmp_path: Path) -> None:
    """DOI 冲突必须保留证据并排除，不得任选重复记录的首个 DOI。"""
    raw_root = tmp_path / "raw"
    audit_dir = tmp_path / "audit"
    output_dir = tmp_path / "gold"
    _write_fixture(raw_root)
    audit_longeval_dataset(raw_root=raw_root, output_dir=audit_dir)

    manifest = import_longeval_gold(raw_root=raw_root, audit_dir=audit_dir, output_dir=output_dir)

    assert manifest.gold_query_count_by_split == {"train": 1, "heldout": 0, "future": 0}
    assert manifest.excluded_query_count_by_split == {"train": 0, "heldout": 1, "future": 1}
    train_gold = load_jsonl(output_dir / "gold.train.jsonl", GoldQuery)
    assert train_gold[0].relevant_papers[0].doi == "10.1000/train"
    evidence = [json.loads(line) for line in (output_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {(item["query_id"], item["status"]) for item in evidence} == {
        ("train-q1", "included"),
        ("heldout-q1", "invalid_doi"),
        ("future-q1", "conflicting_doi"),
    }
    excluded = [json.loads(line) for line in (output_dir / "excluded.jsonl").read_text(encoding="utf-8").splitlines()]
    assert excluded[1]["exclusion_reasons"] == ["conflicting_doi"]
    for filename, expected_hash in manifest.output_sha256.items():
        assert hashlib.sha256((output_dir / filename).read_bytes()).hexdigest() == expected_hash


def test_import_rejects_raw_change_after_audit_and_existing_output(tmp_path: Path) -> None:
    """审计之后的原始文件变更必须阻止导入，已有结果也不得被覆盖。"""
    raw_root = tmp_path / "raw"
    audit_dir = tmp_path / "audit"
    _write_fixture(raw_root)
    audit_longeval_dataset(raw_root=raw_root, output_dir=audit_dir)
    queries_path = raw_root / "train" / "longeval_sci_training_2025_abstract" / "queries.txt"
    queries_path.write_text("train-q1\tchanged query\n", encoding="utf-8")
    with pytest.raises(LongEvalGoldImportError, match="SHA-256 与审计报告不一致"):
        import_longeval_gold(raw_root=raw_root, audit_dir=audit_dir, output_dir=tmp_path / "gold")

    existing_output = tmp_path / "existing"
    existing_output.mkdir()
    with pytest.raises(FileExistsError, match="输出目录已存在"):
        import_longeval_gold(raw_root=raw_root, audit_dir=audit_dir, output_dir=existing_output)


def test_cli_import_is_offline_and_reports_split_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 应只使用本地审计、raw 与新输出目录，且输出分 split 摘要。"""
    raw_root = tmp_path / "raw"
    audit_dir = tmp_path / "audit"
    output_dir = tmp_path / "gold"
    _write_fixture(raw_root)
    audit_longeval_dataset(raw_root=raw_root, output_dir=audit_dir)

    assert main(["longeval-gold-import", "--raw-root", str(raw_root), "--audit-dir", str(audit_dir), "--output-dir", str(output_dir)]) == 0
    assert "学术 API=0，LLM=0，本地模型=0" in capsys.readouterr().out
