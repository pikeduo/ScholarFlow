"""编排 LongEval DOI-strict Track 的本地读取、评分与原子报告发布。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from evaluation.contracts.doi_track import DoiTrackSummary
from evaluation.contracts.gold import GoldQuery
from evaluation.contracts.prediction import PredictionRecord
from evaluation.metrics.doi_track import MATCHING_POLICY, score_doi_track
from evaluation.runners.fixture import load_jsonl


def score_doi_track_to_files(*, gold_path: Path, predictions_path: Path, output_dir: Path, cutoffs: tuple[int, ...] = (5, 10, 20)) -> DoiTrackSummary:
    """读取本地 DOI Gold 与预测，拒绝覆盖地发布 JSON、JSONL 和 Markdown 报告。"""
    normalized_output_dir = output_dir.expanduser().resolve()
    if normalized_output_dir.exists():
        raise FileExistsError(f"DOI Track 报告目录已存在：{normalized_output_dir}")
    gold_queries = load_jsonl(gold_path, GoldQuery)
    predictions = load_jsonl(predictions_path, PredictionRecord)
    summary = score_doi_track(gold_queries, predictions, cutoffs=cutoffs)
    _write_reports(normalized_output_dir, summary)
    return summary


def _write_reports(output_dir: Path, summary: DoiTrackSummary) -> None:
    """在同级临时目录生成完整报告后原子发布，避免部分评分结果被使用。"""
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = _temporary_directory(output_dir.parent, output_dir.name)
    try:
        (temporary_dir / "report.json").write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (temporary_dir / "query_metrics.jsonl").write_text("".join(item.model_dump_json() + "\n" for item in summary.query_metrics), encoding="utf-8")
        (temporary_dir / "report.md").write_text(_render_markdown(summary), encoding="utf-8")
        os.replace(temporary_dir, output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def _render_markdown(summary: DoiTrackSummary) -> str:
    """渲染只陈述 DOI-strict 本地指标的紧凑 Markdown 报告。"""
    lines = [
        "# LongEval DOI-strict 离线评分报告",
        "",
        f"- Matching policy：`{MATCHING_POLICY}`",
        f"- Gold Query：{summary.query_count}",
        f"- 具有预测的 Query：{summary.predicted_query_count}",
        f"- Prediction DOI coverage：{summary.prediction_doi_coverage:.2%}",
        "",
        "| Top-K | Macro P | Macro R | Macro F1 | Micro P | Micro R | Micro F1 | Mean nDCG | Hit Query | Zero-Hit Rate |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for k, item in summary.cutoffs.items():
        lines.append(f"| {k} | {item.macro_precision:.4f} | {item.macro_recall:.4f} | {item.macro_f1:.4f} | {item.micro_precision:.4f} | {item.micro_recall:.4f} | {item.micro_f1:.4f} | {item.mean_ndcg:.4f} | {item.hit_query_count} | {item.zero_hit_query_rate:.2%} |")
    lines.extend(["", f"Mean MRR：{summary.mean_mrr:.4f}", "", "## 边界", "", *[f"- {warning}" for warning in summary.warnings], ""])
    return "\n".join(lines)


def _temporary_directory(parent: Path, label: str) -> Path:
    for _ in range(10):
        candidate = parent / f".{label}.{uuid4().hex}.tmp"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError("无法创建 DOI Track 报告临时目录")
