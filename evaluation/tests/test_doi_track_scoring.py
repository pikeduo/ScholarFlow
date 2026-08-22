"""测试 DOI-strict scorer 不会回退到标题或其他标识。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.cli import main
from evaluation.contracts.common import EvaluationPaper
from evaluation.contracts.gold import GoldQuery
from evaluation.contracts.prediction import PredictionRecord
from evaluation.metrics.doi_track import score_doi_track
from evaluation.runners.dataset_import import serialize_gold_queries
from evaluation.runners.doi_track_scoring import score_doi_track_to_files


def _gold() -> list[GoldQuery]:
    """构造两条带唯一 DOI Gold 的离线查询。"""
    return [
        GoldQuery(query_id="q1", query="first", relevant_papers=[EvaluationPaper(doi="10.1000/a"), EvaluationPaper(doi="10.1000/b")]),
        GoldQuery(query_id="q2", query="second", relevant_papers=[EvaluationPaper(doi="10.1000/c")]),
    ]


def _predictions() -> list[PredictionRecord]:
    """构造含无 DOI、命中、重复 DOI 和非命中 DOI 的有序预测。"""
    return [
        PredictionRecord(
            query_id="q1",
            papers=[
                EvaluationPaper(title="same title but no DOI"),
                EvaluationPaper(doi="DOI:10.1000/B"),
                EvaluationPaper(doi="10.1000/b"),
                EvaluationPaper(doi="10.1000/x"),
            ],
        )
    ]


def test_doi_track_only_matches_valid_doi_and_preserves_original_rank() -> None:
    """缺 DOI 标题和重复 DOI 均不能命中或扩大 Precision 分母。"""
    summary = score_doi_track(_gold(), _predictions(), cutoffs=(2, 5))

    q1, q2 = summary.query_metrics
    assert q1.mrr == 0.5
    assert q1.invalid_or_missing_prediction_doi_count == 1
    assert q1.duplicate_prediction_doi_count == 1
    assert q1.cutoffs[2].true_positive == 1
    assert q1.cutoffs[2].predicted_doi_count == 1
    assert q1.cutoffs[2].precision == 1.0
    assert q1.cutoffs[2].recall == 0.5
    assert q1.cutoffs[5].predicted_doi_count == 2
    assert q1.cutoffs[5].precision == 0.5
    assert q2.missing_prediction is True
    assert q2.cutoffs[5].hit is False
    assert summary.cutoffs[5].macro_f1 == 0.25
    assert summary.prediction_doi_coverage == 0.5


def test_doi_track_rejects_invalid_gold_and_extra_prediction_query() -> None:
    """Gold DOI 必须完整且预测 query 集合不得超出评分分母。"""
    invalid_gold = [GoldQuery(query_id="q", query="query", relevant_papers=[EvaluationPaper(title="no doi")])]
    with pytest.raises(ValueError, match="缺失或非法 DOI"):
        score_doi_track(invalid_gold, [])
    with pytest.raises(ValueError, match="未出现在 DOI Gold"):
        score_doi_track(_gold(), [PredictionRecord(query_id="extra")])


def test_score_to_files_and_cli_are_offline_and_refuse_existing_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """文件 runner 与 CLI 应只读 JSONL 并通过原子目录发布报告。"""
    gold_path = tmp_path / "gold.jsonl"
    prediction_path = tmp_path / "predictions.jsonl"
    gold_path.write_text(serialize_gold_queries(_gold()), encoding="utf-8")
    prediction_path.write_text("".join(item.model_dump_json() + "\n" for item in _predictions()), encoding="utf-8")
    output_dir = tmp_path / "report"

    summary = score_doi_track_to_files(gold_path=gold_path, predictions_path=prediction_path, output_dir=output_dir, cutoffs=(5,))

    assert summary.query_count == 2
    assert json.loads((output_dir / "report.json").read_text(encoding="utf-8"))["matching_policy"] == "doi-strict-v1"
    with pytest.raises(FileExistsError, match="报告目录已存在"):
        score_doi_track_to_files(gold_path=gold_path, predictions_path=prediction_path, output_dir=output_dir)
    cli_output = tmp_path / "cli-report"
    assert main(["doi-track-score", "--gold", str(gold_path), "--predictions", str(prediction_path), "--output-dir", str(cli_output)]) == 0
    assert "学术 API=0，LLM=0，本地模型=0" in capsys.readouterr().out
