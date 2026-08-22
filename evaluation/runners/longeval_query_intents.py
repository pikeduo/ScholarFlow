"""从已封存 LongEval Gold 子集生成可审阅的直接 QueryIntent 与来源调用预估。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from backend.app.models.query_intent import QueryIntent
from evaluation.contracts.gold import GoldQuery
from evaluation.contracts.subset import GoldSubsetManifest
from evaluation.runners.fixture import load_jsonl
from evaluation.runners.usage_forecast import forecast_snapshot_export


class LongEvalQueryIntentPreparationError(RuntimeError):
    """表示 Dev 子集、封存 manifest 或 QueryIntent 计划边界不一致。"""


def prepare_longeval_query_intents(*, gold_path: Path, subset_manifest_path: Path, output_dir: Path, plan_id: str, source_recall_count: int, target_paper_count: int) -> dict[str, object]:
    """生成 direct-query QueryIntent、逐条预估及一个不可覆盖的审阅计划目录。

    该函数不会调用 Query Agent、学术来源或本地模型。每个 QueryIntent 只携带 LongEval
    提供的原始 query，所有主题、方法、子查询与约束字段保持为空，防止导入阶段猜测检索策略。
    """
    normalized_output_dir = output_dir.expanduser().resolve()
    if normalized_output_dir.exists():
        raise FileExistsError(f"LongEval QueryIntent 计划目录已存在：{normalized_output_dir}")
    normalized_plan_id = _normalize_plan_id(plan_id)
    if source_recall_count < 1 or target_paper_count < 1:
        raise ValueError("source_recall_count 与 target_paper_count 必须为正整数")
    if source_recall_count > 100 or target_paper_count > 100:
        raise ValueError("source_recall_count 与 target_paper_count 不能超过 100")
    subset_bytes = subset_manifest_path.read_bytes()
    try:
        subset_manifest = GoldSubsetManifest.model_validate_json(subset_bytes)
    except ValueError as exc:
        raise LongEvalQueryIntentPreparationError("subset manifest 不符合 gold-subset-manifest-v1") from exc
    gold_bytes = gold_path.read_bytes()
    if hashlib.sha256(gold_bytes).hexdigest() != subset_manifest.selected_gold_sha256:
        raise LongEvalQueryIntentPreparationError("GoldQuery SHA-256 与 subset manifest 不一致")
    gold_queries = load_jsonl(gold_path, GoldQuery)
    query_ids = [query.query_id for query in gold_queries]
    if query_ids != subset_manifest.selected_query_ids:
        raise LongEvalQueryIntentPreparationError("GoldQuery query_id 顺序与 subset manifest 不一致")

    normalized_output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = _temporary_directory(normalized_output_dir.parent, normalized_output_dir.name)
    try:
        query_intent_files: dict[str, str] = {}
        export_plan: list[dict[str, object]] = []
        for ordinal, gold_query in enumerate(gold_queries, start=1):
            filename = f"{ordinal:03d}_{_safe_filename_fragment(gold_query.query_id)}.query-intent.json"
            query_intent_path = temporary_dir / "query-intents" / filename
            query_intent_path.parent.mkdir(parents=True, exist_ok=True)
            query_intent = _direct_query_intent(gold_query, source_recall_count=source_recall_count, target_paper_count=target_paper_count)
            query_intent_path.write_text(query_intent.model_dump_json(indent=2) + "\n", encoding="utf-8")
            snapshot_id = f"{normalized_plan_id}-{ordinal:03d}"
            forecast_path = temporary_dir / "forecasts" / f"{ordinal:03d}.snapshot-export.forecast.json"
            forecast_path.parent.mkdir(parents=True, exist_ok=True)
            forecast = forecast_snapshot_export(query_intent_path=query_intent_path, query_id=gold_query.query_id, snapshot_id=snapshot_id, output_path=forecast_path)
            query_intent_files[gold_query.query_id] = (normalized_output_dir / "query-intents" / filename).as_posix()
            export_plan.append({
                "query_id": gold_query.query_id,
                "snapshot_id": snapshot_id,
                "query_intent_file": query_intent_files[gold_query.query_id],
                "forecast_file": (normalized_output_dir / "forecasts" / forecast_path.name).as_posix(),
                "confirmation_sha256": forecast["confirmation_sha256"],
                "academic_api_calls_upper_bound": forecast["academic_api_calls"],
                "actual_http_request_upper_bound": forecast["actual_http_request_upper_bound"],
            })
        manifest = {
            "schema_version": "query-intent-manifest-v1",
            "generation_strategy": "direct-longeval-query-v1",
            "plan_id": normalized_plan_id,
            "source_gold_sha256": hashlib.sha256(gold_bytes).hexdigest(),
            "subset_manifest_sha256": hashlib.sha256(subset_bytes).hexdigest(),
            "query_id_order": query_ids,
            "query_intent_files": query_intent_files,
            "snapshot_export_plan": export_plan,
            "academic_api_calls_upper_bound": sum(item["academic_api_calls_upper_bound"] for item in export_plan),
            "actual_http_request_upper_bound": sum(item["actual_http_request_upper_bound"] for item in export_plan),
            "deepseek_calls": 0,
            "local_model_calls": 0,
            "assumptions": ["QueryIntent 直接使用 LongEval 原始 query，不经 Query Agent、翻译或扩写。", "每条快照仅第一轮候选生成，source_recall_count 与 target_paper_count 已冻结。", "本 manifest 仅供人工审阅；真实来源调用仍需逐条显式提供 --allow-online-sources 和对应 confirmation_sha256。"],
        }
        (temporary_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary_dir, normalized_output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return manifest


def _direct_query_intent(gold_query: GoldQuery, *, source_recall_count: int, target_paper_count: int) -> QueryIntent:
    """构造没有任何推断性检索条件的直接 Dataset QueryIntent。"""
    query = gold_query.query.strip()
    if not query:
        raise LongEvalQueryIntentPreparationError(f"GoldQuery {gold_query.query_id} 的 query 不能为空")
    return QueryIntent(
        original_query=query,
        normalized_query=query,
        query_language=_infer_language(query),
        source_recall_count=source_recall_count,
        target_paper_count=target_paper_count,
        retrieval_round=1,
        search_mode="standard",
        enable_semantic_ranking=False,
        enable_cross_encoder_ranking=False,
        requires_web_evidence=False,
    )


def _infer_language(query: str) -> str:
    """仅按字符范围标记语言，不翻译或解释查询语义。"""
    has_cjk = any("\u4e00" <= character <= "\u9fff" for character in query)
    has_latin = any(character.isascii() and character.isalpha() for character in query)
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    return "en"


def _normalize_plan_id(plan_id: str) -> str:
    """校验用户明确提供的计划标识可作为快照 ID 前缀。"""
    normalized = plan_id.strip()
    if not normalized or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", normalized):
        raise ValueError("plan_id 必须为 1–64 位小写字母、数字或连字符，且以字母或数字开头")
    return normalized


def _safe_filename_fragment(query_id: str) -> str:
    """将 UUID 等 query_id 转为 Windows 安全、稳定的文件名片段。"""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", query_id).strip("._")
    return normalized or "query"


def _temporary_directory(parent: Path, label: str) -> Path:
    for _ in range(10):
        candidate = parent / f".{label}.{uuid4().hex}.tmp"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise LongEvalQueryIntentPreparationError("无法创建 LongEval QueryIntent 临时目录")
