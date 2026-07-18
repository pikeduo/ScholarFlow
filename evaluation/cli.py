"""提供只读取本地文件的离线 fixture 评测命令。"""

import argparse  # 解析明确的本地输入与输出参数。
from pathlib import Path  # 规范化用户传入路径。

from evaluation.runners.fixture import run_fixture  # 调用完全离线运行入口。


def build_parser() -> argparse.ArgumentParser:
    """构建不包含下载、API 或模型命令的参数解析器。"""
    parser = argparse.ArgumentParser(description="ScholarFlow 完全离线评测工具")  # 创建根命令。
    subparsers = parser.add_subparsers(dest="command", required=True)  # 第一阶段只暴露 fixture 子命令。
    fixture_parser = subparsers.add_parser("fixture", help="读取本地 JSONL fixture 并生成报告")  # 创建离线 fixture 命令。
    fixture_parser.add_argument("--gold", type=Path, required=True, help="本地金标 JSONL 路径")  # 要求显式金标文件。
    fixture_parser.add_argument("--predictions", type=Path, required=True, help="本地预测 JSONL 路径")  # 要求显式预测文件。
    fixture_parser.add_argument("--output-dir", type=Path, required=True, help="本地报告输出目录")  # 要求显式输出目录。
    fixture_parser.add_argument("--config", type=Path, default=None, help="可选的本地评测 JSON 配置")  # 允许调整 Top-K 和代理阈值。
    return parser  # 返回可测试解析器。


def main(argv: list[str] | None = None) -> int:
    """运行指定离线命令并返回进程退出码。"""
    args = build_parser().parse_args(argv)  # 解析调用参数。
    if args.command == "fixture":  # 第一阶段唯一受支持命令。
        summary = run_fixture(args.gold, args.predictions, args.output_dir, args.config)  # 只读取本地文件并写本地报告。
        print(f"[OK] 离线评测完成：{summary.retrieval.query_count} 条查询，报告目录 {args.output_dir}")  # 输出不含查询正文的安全摘要。
        return 0  # 表示运行成功。
    raise ValueError(f"不支持的命令: {args.command}")  # 防止未来分支静默忽略。
