"""测试 LongEval Dev QueryIntent 审阅计划只从封存 Gold 直接生成。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.models.query_intent import QueryIntent
from evaluation.cli import main
from evaluation.contracts.common import EvaluationPaper
from evaluation.contracts.gold import GoldQuery
from evaluation.runners.dataset_import import write_gold_queries
from evaluation.runners.gold_subset import select_gold_subset_to_files
from evaluation.runners.longeval_query_intents import LongEvalQueryIntentPreparationError, prepare_longeval_query_intents
from evaluation.runners.usage_forecast import validate_approved_forecast


def _prepare_subset(tmp_path: Path) -> tuple[Path, Path]:
    """生成与真实 Dev20 相同契约的最小封存 Gold 和 manifest。"""
    source = tmp_path / "source.gold.jsonl"
    write_gold_queries(
        [
            GoldQuery(query_id="q-1", query="English retrieval query", relevant_papers=[EvaluationPaper(doi="10.1000/a")]),
            GoldQuery(query_id="q-2", query="中文 检索 query", relevant_papers=[EvaluationPaper(doi="10.1000/b")]),
        ],
        source,
    )
    subset = tmp_path / "subset.gold.jsonl"
    manifest = tmp_path / "subset.manifest.json"
    select_gold_subset_to_files(source, count=2, selection_id="test-dev", selection_seed="test-seed", output_path=subset, manifest_path=manifest)
    return subset, manifest


def test_prepare_generates_direct_query_intents_and_valid_per_query_forecasts(tmp_path: Path) -> None:
    """计划不得猜测主题或调用模型，且每条 forecast 要绑定对应 QueryIntent 与快照 ID。"""
    gold_path, subset_manifest_path = _prepare_subset(tmp_path)
    output_dir = tmp_path / "plan"

    manifest = prepare_longeval_query_intents(gold_path=gold_path, subset_manifest_path=subset_manifest_path, output_dir=output_dir, plan_id="longeval-dev20-v1", source_recall_count=50, target_paper_count=20)

    assert manifest["generation_strategy"] == "direct-longeval-query-v1"
    assert manifest["source_recall_count"] == 50
    assert manifest["target_paper_count"] == 20
    assert manifest["academic_api_calls_upper_bound"] == 2
    assert manifest["actual_http_request_upper_bound"] == 8
    for entry in manifest["snapshot_export_plan"]:
        query_path = Path(entry["query_intent_file"])
        query_intent = QueryIntent.model_validate_json(query_path.read_text(encoding="utf-8"))
        assert query_intent.original_query == query_intent.normalized_query
        assert query_intent.research_topics == [] and query_intent.subqueries == []
        assert query_intent.source_recall_count == 50 and query_intent.target_paper_count == 20
        validate_approved_forecast(forecast_path=Path(entry["forecast_file"]), confirmation_sha256=entry["confirmation_sha256"], operation="snapshot-export", input_path=query_path, query_ids=[entry["query_id"]], snapshot_id=entry["snapshot_id"])


def test_prepare_rejects_gold_manifest_mismatch_and_existing_output(tmp_path: Path) -> None:
    """任何 Gold 哈希漂移或已有计划目录都必须在生成计划前失败。"""
    gold_path, subset_manifest_path = _prepare_subset(tmp_path)
    gold_path.write_text(gold_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(LongEvalQueryIntentPreparationError, match="SHA-256"):
        prepare_longeval_query_intents(gold_path=gold_path, subset_manifest_path=subset_manifest_path, output_dir=tmp_path / "plan", plan_id="longeval-dev20-v1", source_recall_count=50, target_paper_count=20)

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError, match="计划目录已存在"):
        prepare_longeval_query_intents(gold_path=gold_path, subset_manifest_path=subset_manifest_path, output_dir=existing, plan_id="longeval-dev20-v1", source_recall_count=50, target_paper_count=20)


def test_cli_prepare_is_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 只生成审阅计划，不访问学术来源、LLM 或本地模型。"""
    gold_path, subset_manifest_path = _prepare_subset(tmp_path)
    output_dir = tmp_path / "plan"
    exit_code = main(["longeval-query-intent-prepare", "--gold", str(gold_path), "--subset-manifest", str(subset_manifest_path), "--output-dir", str(output_dir), "--plan-id", "longeval-dev20-v1", "--source-recall-count", "50", "--target-paper-count", "20"])
    assert exit_code == 0
    assert "学术 API 上限=2" in capsys.readouterr().out
