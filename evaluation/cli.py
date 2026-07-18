"""提供只读取本地文件的离线 fixture 评测命令。"""

import argparse  # 解析明确的本地输入与输出参数。
from pathlib import Path  # 规范化用户传入路径。

from evaluation.runners.fixture import run_fixture  # 调用完全离线运行入口。
from evaluation.runners.offline_ranking import build_ablation_plan, load_ablation_matrix, write_ablation_plan  # 生成不执行模型的消融计划。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 只读校验候选快照。


def build_parser() -> argparse.ArgumentParser:
    """构建不包含下载、API 或模型命令的参数解析器。"""
    parser = argparse.ArgumentParser(description="ScholarFlow 完全离线评测工具")  # 创建根命令。
    subparsers = parser.add_subparsers(dest="command", required=True)  # 只暴露完全离线命令。
    fixture_parser = subparsers.add_parser("fixture", help="读取本地 JSONL fixture 并生成报告")  # 创建离线 fixture 命令。
    fixture_parser.add_argument("--gold", type=Path, required=True, help="本地金标 JSONL 路径")  # 要求显式金标文件。
    fixture_parser.add_argument("--predictions", type=Path, required=True, help="本地预测 JSONL 路径")  # 要求显式预测文件。
    fixture_parser.add_argument("--output-dir", type=Path, required=True, help="本地报告输出目录")  # 要求显式输出目录。
    fixture_parser.add_argument("--config", type=Path, default=None, help="可选的本地评测 JSON 配置")  # 允许调整 Top-K 和代理阈值。
    snapshot_parser = subparsers.add_parser("snapshot-check", help="只读校验候选快照契约、去重和 SHA-256")  # 创建快照检查命令。
    snapshot_parser.add_argument("--snapshots", type=Path, required=True, help="本地候选快照 JSONL 路径")  # 要求用户显式指定快照文件。
    plan_parser = subparsers.add_parser("ablation-plan", help="组合本地快照和矩阵，只生成零 API、零 DeepSeek 计划")  # 创建消融计划命令。
    plan_parser.add_argument("--snapshots", type=Path, required=True, help="本地候选快照 JSONL 路径")  # 只读加载共享候选。
    plan_parser.add_argument("--matrix", type=Path, required=True, help="本地 A/B/C/D 矩阵 JSON 路径")  # 只读加载排序配置。
    plan_parser.add_argument("--output", type=Path, required=True, help="本地计划 JSON 输出路径")  # 要求显式输出文件。
    return parser  # 返回可测试解析器。


def main(argv: list[str] | None = None) -> int:
    """运行指定离线命令并返回进程退出码。"""
    args = build_parser().parse_args(argv)  # 解析调用参数。
    if args.command == "fixture":  # 第一阶段唯一受支持命令。
        summary = run_fixture(args.gold, args.predictions, args.output_dir, args.config)  # 只读取本地文件并写本地报告。
        print(f"[OK] 离线评测完成：{summary.retrieval.query_count} 条查询，报告目录 {args.output_dir}")  # 输出不含查询正文的安全摘要。
        return 0  # 表示运行成功。
    if args.command == "snapshot-check":  # 第二阶段只读验证候选快照。
        snapshots = load_candidate_snapshots(args.snapshots)  # 核验契约、身份去重和哈希。
        print(f"[OK] 候选快照校验完成：{len(snapshots)} 份，未执行学术 API、LLM 或本地模型")  # 输出不含查询和论文正文的安全摘要。
        return 0  # 表示校验成功。
    if args.command == "ablation-plan":  # 第二阶段生成不执行模型的本地任务计划。
        snapshots = load_candidate_snapshots(args.snapshots)  # 只读加载并核验共享候选。
        matrix = load_ablation_matrix(args.matrix)  # 加载统一来源召回和评分配置。
        plan = build_ablation_plan(snapshots, matrix)  # 组合快照和矩阵但不调用打分器。
        write_ablation_plan(plan, args.output)  # 写出用户指定计划文件。
        print(f"[OK] 离线消融计划完成：{plan.task_count} 个任务，学术 API=0，DeepSeek=0")  # 明确资源边界。
        return 0  # 表示计划生成成功。
    raise ValueError(f"不支持的命令: {args.command}")  # 防止未来分支静默忽略。
