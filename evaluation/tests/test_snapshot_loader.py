"""测试排序前候选快照的结构、哈希、只读加载和身份去重。"""

from datetime import datetime, timezone  # 构造带时区的快照时间。
from pathlib import Path  # 定位内置 fixture 并写入 pytest 临时文件。

import pytest  # 验证篡改和重复候选错误。

from evaluation.contracts.snapshot import CandidatePaper, CandidateSnapshot, seal_snapshot  # 构造并封存候选快照。
from evaluation.runners.snapshot_loader import load_candidate_snapshots, validate_snapshot_integrity  # 执行只读完整性校验。


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]  # 稳定定位仓库根目录。
SNAPSHOT_FIXTURE = REPOSITORY_ROOT / "evaluation" / "fixtures" / "candidate_snapshots.jsonl"  # 定位已封存合成快照。


def _snapshot(papers: list[CandidatePaper]) -> CandidateSnapshot:
    """构造与测试候选数量一致的未封存快照。"""
    return CandidateSnapshot(snapshot_id="snapshot-test", query_id="q-test", query="test retrieval", query_intent={"normalized_query": "test retrieval"}, source_recall_count=50, target_paper_count=2, sources_used=["openalex"], raw_candidate_count=len(papers), normalized_candidate_count=len(papers), deduplicated_candidate_count=len(papers), papers=papers, created_at=datetime(2026, 7, 18, tzinfo=timezone.utc))  # 使用单来源和连续 RRF 排名。


def test_built_in_candidate_snapshot_is_sealed_and_read_only() -> None:
    """内置合成快照应通过契约、去重和内容哈希校验。"""
    snapshots = load_candidate_snapshots(SNAPSHOT_FIXTURE)  # 以默认严格模式只读加载。
    assert len(snapshots) == 1  # fixture 只包含一条查询。
    assert snapshots[0].snapshot_stage == "normalized_deduplicated_rrf"  # 明确是本地排序前候选。
    assert snapshots[0].deduplicated_candidate_count == 4  # 候选数量与实际论文列表一致。
    assert validate_snapshot_integrity(snapshots[0]) == snapshots[0].snapshot_hash  # 声明哈希与实际内容一致。


def test_tampered_snapshot_hash_is_rejected() -> None:
    """封存后修改查询或候选内容必须导致哈希校验失败。"""
    paper = CandidatePaper(paper_id="p1", doi="10.1/p1", title="Paper One", source="openalex", rrf_score=0.02, snapshot_rank=1)  # 构造唯一候选。
    sealed = seal_snapshot(_snapshot([paper]))  # 固化原始内容摘要。
    tampered = sealed.model_copy(update={"query": "changed query"})  # 模拟文件内容被修改但未更新哈希。
    with pytest.raises(ValueError, match="snapshot_hash 不匹配"):  # 验证篡改被明确拒绝。
        validate_snapshot_integrity(tampered)


def test_duplicate_candidate_identity_is_rejected_after_structural_validation() -> None:
    """不同 paper_id 但相同 DOI 的候选不得进入排序前快照。"""
    papers = [CandidatePaper(paper_id="p1", doi="10.1/same", title="First", source="openalex", rrf_score=0.03, snapshot_rank=1), CandidatePaper(paper_id="p2", doi="DOI:10.1/SAME", title="Duplicate", source="openalex", rrf_score=0.02, snapshot_rank=2)]  # 构造跨格式重复 DOI。
    sealed = seal_snapshot(_snapshot(papers))  # 结构层允许后交给统一身份规则判断。
    with pytest.raises(ValueError, match="含重复论文"):  # 验证去重承诺被执行。
        validate_snapshot_integrity(sealed)


def test_snapshot_requires_contiguous_rrf_ranks_and_timezone() -> None:
    """快照排名断裂或时间无时区时应在契约层拒绝。"""
    paper = CandidatePaper(paper_id="p1", title="Paper", source="openalex", rrf_score=0.02, snapshot_rank=2)  # 构造错误起始排名。
    with pytest.raises(ValueError, match="snapshot_rank"):  # 验证排名边界。
        _snapshot([paper])
    valid_paper = paper.model_copy(update={"snapshot_rank": 1})  # 修正候选排名。
    with pytest.raises(ValueError, match="created_at 必须包含明确时区"):  # 验证时间边界。
        CandidateSnapshot(snapshot_id="snapshot-time", query_id="q-time", query="test", source_recall_count=50, target_paper_count=1, sources_used=["openalex"], raw_candidate_count=1, normalized_candidate_count=1, deduplicated_candidate_count=1, papers=[valid_paper], created_at=datetime(2026, 7, 18))


def test_empty_snapshot_file_is_rejected(tmp_path: Path) -> None:
    """空 JSONL 不得被误报为已完成快照校验。"""
    empty_path = tmp_path / "empty.jsonl"  # 使用 pytest 临时目录构造空输入。
    empty_path.write_text("\n", encoding="utf-8")  # 写入不含有效记录的 UTF-8 文件。
    with pytest.raises(ValueError, match="不包含有效记录"):  # 验证清晰边界错误。
        load_candidate_snapshots(empty_path)
