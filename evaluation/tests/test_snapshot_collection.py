"""测试候选快照集合组装只复用本地已封存输入且拒绝重试歧义。"""

import json  # 写入最小 QueryIntent manifest fixture。
from datetime import datetime, timezone  # 构造带明确时区的候选快照时间。
from pathlib import Path  # 使用 pytest 临时目录隔离所有本地文件。

import pytest  # 验证歧义重试和不安全覆盖边界。

from backend.app.models.query_intent import QueryIntent  # 构造早期计划兼容场景的真实 QueryIntent 文件。
from evaluation.contracts.snapshot import CandidatePaper, CandidateSnapshot, seal_snapshot  # 构造已封存的单查询候选快照。
from evaluation.runners.snapshot_collection import assemble_candidate_snapshot_collection, parse_snapshot_overrides  # 调用纯本地集合组装入口。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 复核输出仍可被正式加载器消费。


QUERY_ONE = "pasa:auto-dev:q-001"  # 构造第一个稳定查询标识。
QUERY_TWO = "pasa:auto-dev:q-002"  # 构造第二个稳定查询标识。


def _snapshot(query_id: str, snapshot_id: str, *, warnings: list[str] | None = None) -> CandidateSnapshot:
    """构造满足排序前快照契约的最小本地候选。"""
    paper = CandidatePaper(  # 每条查询构造唯一的规范化论文，避免跨快照身份规则干扰本用例。
        paper_id=f"paper-{snapshot_id}",  # 提供快照内稳定论文标识。
        title=f"Paper {snapshot_id}",  # 提供非空展示标题。
        source="openalex",  # 保持与 PaSa 在线候选相同的学术来源名称。
        rrf_score=0.1,  # 构造合法的非负 RRF 分数。
        snapshot_rank=1,  # 单候选必须从一开始连续排名。
    )
    snapshot = CandidateSnapshot(  # 构造规则过滤后、BGE-M3 前的最小封存对象。
        snapshot_id=snapshot_id,
        query_id=query_id,
        query=f"query for {query_id}",
        query_intent={"source_recall_count": 50, "target_paper_count": 20},
        source_recall_count=50,
        target_paper_count=20,
        sources_used=["openalex"],
        normalized_candidate_count=1,
        deduplicated_candidate_count=1,
        filtered_candidate_count=0,
        ranking_candidate_count=1,
        source_counts={"openalex": 1},
        papers=[paper],
        warnings=list(warnings or []),
        created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    return seal_snapshot(snapshot)  # 写入真实内容哈希供正式加载器校验。


def _write_snapshot(path: Path, snapshot: CandidateSnapshot) -> None:
    """将单份快照写成 snapshot-export 同样使用的一行 JSONL 形式。"""
    path.write_text(snapshot.model_dump_json() + "\n", encoding="utf-8")  # 仅为 pytest 临时 fixture 写入本地文件。


def _write_query_intent_manifest(path: Path, *, include_counts: bool = True) -> None:
    """写入只包含集合组装所需字段的已确认 QueryIntent manifest fixture。"""
    payload = {  # 保持真实 manifest 的版本、参数、顺序与文件映射结构。
        "schema_version": "query-intent-manifest-v1",
        "query_id_order": [QUERY_ONE, QUERY_TWO],
        "query_intent_files": {QUERY_ONE: "q-001.json", QUERY_TWO: "q-002.json"},
    }
    if include_counts:  # 默认覆盖当前 manifest 契约，也为早期计划兼容场景留出 fixture。
        payload["source_recall_count"] = 50
        payload["target_paper_count"] = 20
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")  # 不读取 .env、网络或真实评测数据。


def _write_query_intents(path: Path) -> None:
    """写入与快照边界一致的 QueryIntent，模拟早期 LongEval 计划的逐条冻结输入。"""
    for filename in ("q-001.json", "q-002.json"):  # 每条冻结查询都必须有对应的本地意图文件。
        query_intent = QueryIntent(  # 只提供集合兼容路径所需的最小合法字段。
            original_query=filename,
            normalized_query=filename,
            query_language="en",
            source_recall_count=50,
            target_paper_count=20,
            retrieval_round=1,
            search_mode="standard",
        )
        (path.parent / filename).write_text(query_intent.model_dump_json(), encoding="utf-8")


def test_assemble_collection_uses_explicit_retry_and_excludes_source_failure_artifact(tmp_path: Path) -> None:
    """同一查询存在成功重试与来源失败产物时，应按显式选择封存唯一成功版本。"""
    snapshot_directory = tmp_path / "snapshots"  # 隔离单查询快照目录。
    snapshot_directory.mkdir()  # 创建测试专用本地目录。
    _write_snapshot(snapshot_directory / "001.retry5.snapshot.jsonl", _snapshot(QUERY_ONE, "snapshot-q1-retry5"))  # 构造第一个有效重试。
    _write_snapshot(snapshot_directory / "001.retry7.snapshot.jsonl", _snapshot(QUERY_ONE, "snapshot-q1-retry7"))  # 构造用户显式确认的最终重试。
    _write_snapshot(snapshot_directory / "001.failed.snapshot.jsonl", _snapshot(QUERY_ONE, "snapshot-q1-failed", warnings=["学术来源降级 openalex: 学术来源调用失败"]))  # 构造必须排除的旧失败产物。
    _write_snapshot(snapshot_directory / "002.snapshot.jsonl", _snapshot(QUERY_TWO, "snapshot-q2"))  # 构造唯一可自动选择的第二条快照。
    query_manifest_path = tmp_path / "query-intents.manifest.json"  # 定位测试 QueryIntent manifest。
    _write_query_intent_manifest(query_manifest_path)  # 写入冻结查询顺序与参数。
    output_path = tmp_path / "collection.jsonl"  # 指定尚不存在的集合 JSONL。
    collection_manifest_path = tmp_path / "collection.manifest.json"  # 指定尚不存在的集合审计 manifest。

    manifest = assemble_candidate_snapshot_collection(  # 执行完全离线组装，不实例化任何生产来源或模型。
        collection_id="pasa-auto-dev-ranking-v1",
        query_intent_manifest_path=query_manifest_path,
        snapshot_directory=snapshot_directory,
        snapshot_overrides=parse_snapshot_overrides([f"{QUERY_ONE}=001.retry7.snapshot.jsonl"]),
        output_path=output_path,
        manifest_path=collection_manifest_path,
    )

    snapshots = load_candidate_snapshots(output_path)  # 使用正式加载器验证集合 JSONL 仍具有完整哈希和身份边界。
    assert [snapshot.query_id for snapshot in snapshots] == [QUERY_ONE, QUERY_TWO]  # 输出必须遵循 QueryIntent manifest 的稳定顺序。
    assert manifest.selected_snapshot_ids[QUERY_ONE] == "snapshot-q1-retry7"  # 显式选择必须覆盖自动目录扫描。
    assert manifest.selected_snapshot_paths[QUERY_ONE] == "001.retry7.snapshot.jsonl"  # manifest 只保存可移植的目录内相对路径。
    assert manifest.ranking_candidate_counts == {QUERY_ONE: 1, QUERY_TWO: 1}  # 真实候选数量必须完整冻结。
    assert collection_manifest_path.exists()  # JSONL 成功后必须同时写出审计 manifest。


def test_assemble_collection_rejects_multiple_successful_retries_without_override(tmp_path: Path) -> None:
    """存在多个无来源降级的有效重试时，不得按文件名或时间猜测选择。"""
    snapshot_directory = tmp_path / "snapshots"  # 创建隔离快照目录。
    snapshot_directory.mkdir()  # 仅创建 pytest 临时输入位置。
    _write_snapshot(snapshot_directory / "001.retry5.snapshot.jsonl", _snapshot(QUERY_ONE, "snapshot-q1-retry5"))  # 构造第一个成功重试。
    _write_snapshot(snapshot_directory / "001.retry7.snapshot.jsonl", _snapshot(QUERY_ONE, "snapshot-q1-retry7"))  # 构造第二个成功重试。
    _write_snapshot(snapshot_directory / "002.snapshot.jsonl", _snapshot(QUERY_TWO, "snapshot-q2"))  # 构造第二条查询唯一快照。
    query_manifest_path = tmp_path / "query-intents.manifest.json"  # 定位测试 QueryIntent manifest。
    _write_query_intent_manifest(query_manifest_path)  # 写入冻结顺序。

    with pytest.raises(ValueError, match="多个有效候选快照"):
        assemble_candidate_snapshot_collection(  # 不提供 override，必须在任何输出创建前拒绝歧义。
            collection_id="pasa-auto-dev-ranking-v1",
            query_intent_manifest_path=query_manifest_path,
            snapshot_directory=snapshot_directory,
            snapshot_overrides={},
            output_path=tmp_path / "collection.jsonl",
            manifest_path=tmp_path / "collection.manifest.json",
        )


def test_assemble_collection_derives_counts_from_early_query_intent_plan(tmp_path: Path) -> None:
    """早期计划缺少顶层汇总参数时，组装器应从已封存的逐条 QueryIntent 只读推导。"""
    snapshot_directory = tmp_path / "snapshots"
    snapshot_directory.mkdir()
    _write_snapshot(snapshot_directory / "001.snapshot.jsonl", _snapshot(QUERY_ONE, "snapshot-q1"))
    _write_snapshot(snapshot_directory / "002.snapshot.jsonl", _snapshot(QUERY_TWO, "snapshot-q2"))
    query_manifest_path = tmp_path / "query-intents.manifest.json"
    _write_query_intent_manifest(query_manifest_path, include_counts=False)
    _write_query_intents(query_manifest_path)

    manifest = assemble_candidate_snapshot_collection(
        collection_id="pasa-auto-dev-ranking-v1",
        query_intent_manifest_path=query_manifest_path,
        snapshot_directory=snapshot_directory,
        snapshot_overrides={},
        output_path=tmp_path / "collection.jsonl",
        manifest_path=tmp_path / "collection.manifest.json",
    )

    assert manifest.source_recall_count == 50
    assert manifest.target_paper_count == 20


def test_parse_snapshot_overrides_rejects_directory_escape() -> None:
    """显式选择只能引用快照目录内相对路径，不能读取其他实验文件。"""
    with pytest.raises(ValueError, match="相对路径"):
        parse_snapshot_overrides([f"{QUERY_ONE}=../other.snapshot.jsonl"])  # 尝试越出快照目录必须被拒绝。
