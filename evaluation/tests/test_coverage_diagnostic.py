"""测试候选覆盖诊断只读取本地金标和排序前快照。"""

import json  # 构造最小 GoldQuery JSONL 输入。
from pathlib import Path  # 定位合成 fixture 与临时输出目录。

import pytest  # 断言输出保护和输入集合边界。

from evaluation.runners.coverage_diagnostic import diagnose_candidate_coverage  # 测试完全离线诊断入口。


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]  # 稳定定位仓库根目录。
FIXTURE_GOLD = REPOSITORY_ROOT / "evaluation" / "fixtures" / "gold.jsonl"  # 复用已有合成金标首条记录。
FIXTURE_SNAPSHOTS = REPOSITORY_ROOT / "evaluation" / "fixtures" / "candidate_snapshots.jsonl"  # 复用已封存合成候选快照。


def test_diagnoses_identity_coverage_without_models_or_network(tmp_path: Path) -> None:
    """诊断应按相同 query_id 比较并区分强标识符命中与零命中。"""
    gold_line = FIXTURE_GOLD.read_text(encoding="utf-8").splitlines()[0]  # 仅保留与单份快照对齐的第一条金标。
    gold_path = tmp_path / "gold.jsonl"  # 创建用户显式提供的最小本地输入。
    gold_path.write_text(gold_line + "\n", encoding="utf-8")  # 保持 JSONL UTF-8 格式。
    output_dir = tmp_path / "diagnostic"  # 选择此前不存在的输出目录。
    summary = diagnose_candidate_coverage(gold_path=gold_path, snapshots_path=FIXTURE_SNAPSHOTS, output_dir=output_dir)  # 只读执行诊断。
    assert summary.query_count == 1  # 验证严格按共同查询集合计数。
    assert summary.matched_gold_paper_count == 2  # fixture 前两篇候选分别以 DOI 和 arXiv 命中金标。
    assert summary.zero_match_query_count == 0  # 当前合成输入不存在零命中查询。
    query_record = json.loads((output_dir / "query_diagnostics.jsonl").read_text(encoding="utf-8"))  # 读取唯一逐查询审计行。
    assert query_record["strong_identifier_match_count"] == 2  # 验证匹配方式来自稳定强标识符。
    assert (output_dir / "diagnostic.md").is_file()  # 验证人工摘要也被写出。
    with pytest.raises(FileExistsError, match="输出目录已存在"):  # 已审阅诊断不得覆盖。
        diagnose_candidate_coverage(gold_path=gold_path, snapshots_path=FIXTURE_SNAPSHOTS, output_dir=output_dir)  # 再次执行必须拒绝。


def test_rejects_mismatched_query_sets(tmp_path: Path) -> None:
    """金标与候选查询集合不一致时不得写出部分诊断。"""
    gold_path = tmp_path / "gold.jsonl"  # 构造不匹配的金标查询标识。
    record = json.loads(FIXTURE_GOLD.read_text(encoding="utf-8").splitlines()[0])  # 读取合成记录后仅修改查询标识。
    record["query_id"] = "other-query"  # 故意制造集合差异。
    gold_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")  # 写入有效但不对齐的金标。
    output_dir = tmp_path / "diagnostic"  # 选择理论输出位置。
    with pytest.raises(ValueError, match="query_id 不一致"):  # 必须明确拒绝部分比较。
        diagnose_candidate_coverage(gold_path=gold_path, snapshots_path=FIXTURE_SNAPSHOTS, output_dir=output_dir)  # 不访问任何外部资源。
    assert not output_dir.exists()  # 验证失败没有生成误导性目录。
