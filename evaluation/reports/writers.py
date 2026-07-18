"""将评测结果写为 UTF-8 JSON、JSONL 与 Markdown。"""

import json  # 序列化可机读报告。
from pathlib import Path  # 创建和定位报告目录。

from evaluation.contracts.result import EvaluationSummary  # 接收完整报告契约。


def _format_optional(value: float | int | None, digits: int = 4) -> str:
    """将缺失值显示为 ``N/A``，数值显示为稳定小数。"""
    if value is None:  # 缺失观测必须明确显示。
        return "N/A"  # 不用零替代缺失值。
    if isinstance(value, int):  # 整数统计无需小数位。
        return str(value)  # 保留原始整数。
    return f"{value:.{digits}f}"  # 浮点指标使用稳定精度。


def render_markdown(summary: EvaluationSummary) -> str:
    """渲染包含代理分免责声明和核心指标表的 Markdown 报告。"""
    lines = [
        "# ScholarFlow 离线评测报告",
        "",
        f"生成时间：{summary.generated_at}",
        "",
        "> 注意：效率分、结构分及综合分均为本地代理分（非官方）。赛题未公开完整官方公式时，不得将这些分数描述为官方成绩。",
        "",
        "## 检索与排序指标",
        "",
        "| Top-K | Macro P | Macro R | Macro F1 | Micro P | Micro R | Micro F1 | Mean nDCG |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]  # 建立报告标题、免责声明和检索表头。
    for k, metrics in summary.retrieval.cutoffs.items():  # 按契约稳定顺序输出各截断。
        lines.append(f"| {k} | {metrics.macro_precision:.4f} | {metrics.macro_recall:.4f} | {metrics.macro_f1:.4f} | {metrics.micro_precision:.4f} | {metrics.micro_recall:.4f} | {metrics.micro_f1:.4f} | {summary.retrieval.mean_ndcg_at_k[k]:.4f} |")  # 输出统一四位小数。
    lines.extend([
        "",
        f"Mean MRR：{summary.retrieval.mean_mrr:.4f}",
        "",
        "## 效率观测与代理分",
        "",
        f"- 学术 API 逻辑调用数：{_format_optional(summary.efficiency.academic_api_calls)}",
        f"- 实际 HTTP 请求数：{_format_optional(summary.efficiency.actual_http_requests)}",
        f"- LLM 调用数：{_format_optional(summary.efficiency.llm_calls)}",
        f"- Token 总数：{_format_optional(summary.efficiency.total_tokens)}",
        f"- 平均耗时（ms）：{_format_optional(summary.efficiency.latency_mean_ms, 2)}",
        f"- P95 耗时（ms）：{_format_optional(summary.efficiency.latency_p95_ms, 2)}",
        f"- {summary.efficiency.proxy_label}：{_format_optional(summary.efficiency.proxy_score)}",
        "",
        "## 结构与综合代理分",
        "",
        f"- {summary.structure.proxy_label}：{summary.structure.mean_proxy_score:.4f}",
        f"- {summary.composite_proxy_label}：{_format_optional(summary.local_composite_proxy_score)}",
        "",
        "## 警告",
        "",
    ])  # 追加效率、结构和综合代理分。
    lines.extend(f"- {warning}" for warning in summary.warnings)  # 输出所有缺失值与代理分警告。
    lines.append("")  # 保证 Markdown 以换行结束。
    return "\n".join(lines)  # 返回可直接写入文件的文本。


def write_reports(summary: EvaluationSummary, output_dir: Path) -> None:
    """在目标目录写出汇总 JSON、查询 JSONL 与 Markdown 报告。"""
    output_dir.mkdir(parents=True, exist_ok=True)  # 只创建用户明确指定的本地报告目录。
    summary_payload = summary.model_dump(mode="json")  # 将日期、整数键等转换为 JSON 兼容值。
    (output_dir / "report.json").write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")  # 写出完整可机读报告。
    query_lines = [json.dumps(metrics.model_dump(mode="json"), ensure_ascii=False) for metrics in summary.query_metrics]  # 逐条序列化查询指标。
    (output_dir / "query_metrics.jsonl").write_text("\n".join(query_lines) + ("\n" if query_lines else ""), encoding="utf-8")  # 写出便于后续统计的 JSONL。
    (output_dir / "report.md").write_text(render_markdown(summary), encoding="utf-8")  # 写出供人工审阅的 Markdown。
