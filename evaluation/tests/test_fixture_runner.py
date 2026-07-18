"""测试内置合成 fixture 的端到端离线报告闭环。"""

import json  # 验证生成的 JSON 和 JSONL 可解析。
from pathlib import Path  # 定位仓库内合成 fixture。

import pytest  # 验证非法输入边界。

from evaluation.contracts.gold import GoldQuery  # 构造重复金标边界。
from evaluation.runners.fixture import evaluate_records, run_fixture  # 运行纯内存与文件评测。


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]  # 从测试文件稳定定位仓库根目录。
FIXTURE_ROOT = REPOSITORY_ROOT / "evaluation" / "fixtures"  # 定位内置纯合成数据。
DEFAULT_CONFIG = REPOSITORY_ROOT / "evaluation" / "config" / "default.json"  # 定位显式评测配置。


def test_synthetic_fixture_writes_three_report_formats(tmp_path: Path) -> None:
    """内置 fixture 应生成可解析 JSON、JSONL 和带免责声明的 Markdown。"""
    output_dir = tmp_path / "reports"  # 使用 pytest 临时目录避免污染仓库结果。
    summary = run_fixture(FIXTURE_ROOT / "gold.jsonl", FIXTURE_ROOT / "predictions.jsonl", output_dir, DEFAULT_CONFIG)  # 完全离线运行合成数据。
    assert summary.retrieval.query_count == 3  # 三条金标都进入评分。
    assert summary.retrieval.predicted_query_count == 2  # 缺失预测仍参与评分但不冒充已预测。
    assert list(summary.retrieval.cutoffs) == [5, 10, 20]  # 默认报告同时包含三个独立评分截断。
    assert summary.local_composite_proxy_score is not None  # 两条真实预测 usage 完整，可生成本地综合代理分。
    report_json = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))  # 验证完整 JSON 可解析。
    assert report_json["composite_proxy_label"] == "本地综合代理分（非官方）"  # JSON 明确代理属性。
    query_lines = (output_dir / "query_metrics.jsonl").read_text(encoding="utf-8").strip().splitlines()  # 读取查询级 JSONL。
    assert len(query_lines) == 3  # 每条金标输出一行明细。
    assert all(json.loads(line)["query_id"] for line in query_lines)  # 每行均可解析且包含稳定标识。
    markdown = (output_dir / "report.md").read_text(encoding="utf-8")  # 读取人工报告。
    assert "本地代理分（非官方）" in markdown  # Markdown 首屏包含代理分免责声明。


def test_duplicate_gold_query_is_rejected() -> None:
    """重复 query_id 会使分母不确定，应在评分前拒绝。"""
    duplicate = GoldQuery(query_id="q1", query="duplicate")  # 构造重复键记录。
    with pytest.raises(ValueError, match="金标 存在重复 query_id: q1"):  # 验证清晰错误。
        evaluate_records([duplicate, duplicate], [])
