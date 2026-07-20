"""测试固定 PaSa 20 条端到端计划与完全离线评分边界。"""

import json  # 验证写出的 JSON 与 JSONL 均可解析。
from pathlib import Path  # 使用 pytest 临时目录隔离输出。

from evaluation.contracts.common import EvaluationPaper  # 构造不访问网络的合成最终论文。
from evaluation.contracts.end_to_end import EndToEndRunRecord, EndToEndUsage, LlmStageUsage  # 构造完整和缺失观测的在线归档。
from evaluation.contracts.gold import GoldQuery  # 构造 PaSa 稀疏金标身份场景。
from evaluation.contracts.prediction import PredictionRecord  # 构造最终 Top 20 预测记录。
from evaluation.metrics.pasa_identity_audit import audit_pasa_query  # 验证离线身份审计不使用模糊匹配。
from evaluation.runners.end_to_end import score_end_to_end, write_execution_plan  # 测试计划生成和离线报告闭环。
from evaluation.runners.fixture import load_jsonl  # 读取仓库内固定 GoldQuery。


ROOT = Path(__file__).resolve().parents[2]  # 从测试文件稳定定位仓库根目录。
GOLD = ROOT / "evaluation" / "inputs" / "pasa-auto-dev-ranking20.gold.jsonl"  # 使用本次固定 PaSa 20 条金标。
MANIFEST = ROOT / "evaluation" / "inputs" / "pasa-auto-dev-ranking20.manifest.json"  # 使用本次固定子集 manifest。


def test_fixed_pasa_plan_and_offline_report_keep_all_twenty_queries(tmp_path: Path) -> None:
    """计划必须冻结二十条，离线报告必须把全部记录保留在固定分母。"""
    plan_path = tmp_path / "plan.jsonl"  # 使用未存在的临时计划输出路径。
    assert write_execution_plan(GOLD, MANIFEST, plan_path) == 20  # 验证不重新抽样且只写二十条。
    plan_rows = [json.loads(line) for line in plan_path.read_text(encoding="utf-8").splitlines()]  # 读取计划以构造离线运行归档。
    gold_by_id = {item.query_id: item for item in load_jsonl(GOLD, GoldQuery)}  # 读取每条固定查询的金标论文。
    runs = []  # 构造二十条无网络运行归档。
    for index, row in enumerate(plan_rows):  # 保持封存顺序构造每条查询。
        gold_paper = gold_by_id[row["query_id"]].relevant_papers[0]  # 取首篇金标以构造确定性命中。
        papers = [EvaluationPaper.model_validate(gold_paper.model_dump())] if index == 0 else []  # 仅首条命中，其余空结果仍进入分母。
        runs.append(EndToEndRunRecord(query_id=row["query_id"], run_id=f"run-{index}", status="completed", papers=papers, usage=EndToEndUsage(academic_api_calls=1, latency_ms=1000 + index, total_estimated_cost_cny=0.01, query_agent=LlmStageUsage(call_count=1, input_tokens=10, output_tokens=5, total_tokens=15), llm_total_tokens=15), graph_requested=bool(papers), graph_generated=bool(papers), graph_node_ids=[papers[0].paper_id] if papers and papers[0].paper_id else []))  # 未观测 HTTP、重试、429 必须保持空而非零。
    runs_path = tmp_path / "runs.jsonl"  # 使用独立在线归档输入路径。
    runs_path.write_text("\n".join(item.model_dump_json() for item in runs) + "\n", encoding="utf-8")  # 只写本地合成 JSONL。
    output_dir = tmp_path / "report"  # 使用临时离线报告目录。
    summary = score_end_to_end(GOLD, MANIFEST, runs_path, output_dir)  # 完全离线生成三种报告。
    assert summary.query_count == 20  # 验证空结果仍进入固定分母。
    assert summary.retrieval["zero_hit_query_count"] == 19  # 只有首条构造了一个金标命中。
    assert "actual_http_requests" in summary.efficiency["missing_fields"]  # 不可观测生产指标不能伪造为零。
    assert (output_dir / "report.json").is_file()  # 验证机读 JSON 报告已写出。
    assert len((output_dir / "query_metrics.jsonl").read_text(encoding="utf-8").splitlines()) == 20  # 验证查询级 JSONL 完整覆盖二十条。
    assert (output_dir / "identity_audit.jsonl").is_file()  # 验证身份审计逐条证据已单独归档。
    assert (output_dir / "observability_audit.json").is_file()  # 验证部分观测范围不会隐藏在 Markdown 中。
    assert "PaSa AutoScholarQuery dev固定20条初步评测" in (output_dir / "report.md").read_text(encoding="utf-8")  # 验证 Markdown 包含指定免责声明。


def test_pasa_identity_audit_accepts_only_deterministic_alias_or_sparse_exact_title() -> None:
    """PaSa 审计只接受 arXiv DOI 别名和缺少消歧字段时的精确标题。"""
    gold = GoldQuery(  # 构造 PaSa 原始格式常见的 arXiv 加标题稀疏金标。
        query_id="pasa:test",  # 使用稳定的本地测试查询标识。
        query="synthetic query",  # 不调用 Query Agent 或学术来源。
        relevant_papers=[  # 同时覆盖 DOI 别名和标题回退两类可审阅证据。
            EvaluationPaper(arxiv_id="2208.00277", title="MobileNeRF: Exploiting the Polygon Rasterization Pipeline for Efficient Neural Field Rendering on Mobile Architectures"),  # DOI 可确定映射的 arXiv 金标。
            EvaluationPaper(arxiv_id="9999.00001", title="Exact Sparse Gold Title"),  # 无年份和作者时允许完全标题回退。
        ],
    )
    prediction = PredictionRecord(  # 构造不访问网络的最终排序列表。
        query_id=gold.query_id,  # 与 Gold 绑定同一查询。
        papers=[  # 两篇命中均应被记录，第三篇近似标题不得命中。
            EvaluationPaper(doi="https://doi.org/10.48550/arXiv.2208.00277", title="Different display title"),  # 验证 DOI 到 arXiv 的固定别名。
            EvaluationPaper(openalex_id="W-test", title="Exact Sparse Gold Title", year=2024),  # 验证 Gold 稀疏时的精确标题回退。
            EvaluationPaper(openalex_id="W-other", title="Exact Sparse Gold Title Extended", year=2024),  # 禁止近似标题误计分。
        ],
    )
    audit = audit_pasa_query(gold, prediction)  # 运行纯本地确定性身份审计。
    assert audit["true_positive"] == 2  # 验证只匹配两篇真实 Gold。
    assert audit["evidence_counts"] == {"arxiv_doi_alias": 1, "exact_title_sparse_gold": 1}  # 验证报告可区分两种修正来源。
