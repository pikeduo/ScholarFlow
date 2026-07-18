"""提供默认离线、仅经显式授权才可在线生成候选快照的评测命令。"""

import argparse  # 解析明确的本地输入与输出参数。
import asyncio  # 仅在候选快照导出分支运行异步生产候选服务。
from collections.abc import Callable  # 为测试注入不访问真实来源的候选服务工厂。
from pathlib import Path  # 规范化用户传入路径。
from typing import Any  # 避免离线命令导入生产候选服务类型。

from evaluation.runners.dataset_import import import_prepared_dataset_gold  # 转换用户本地准备的数据集金标而不下载原始数据。
from evaluation.runners.fixture import run_fixture  # 调用完全离线运行入口。
from evaluation.runners.offline_ranking import build_ablation_plan, load_ablation_matrix, write_ablation_plan  # 生成不执行模型的消融计划。
from evaluation.runners.pasa_import import import_pasa_gold  # 将用户已下载的确认版 PaSa 原始 JSONL 转换为统一金标。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 只读校验候选快照。


def build_parser() -> argparse.ArgumentParser:
    """构建默认离线并隔离唯一受控在线入口的参数解析器。"""
    parser = argparse.ArgumentParser(description="ScholarFlow 离线评测与受控候选快照工具")  # 创建根命令。
    subparsers = parser.add_subparsers(dest="command", required=True)  # 明确区分离线命令和受控在线命令。
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
    dataset_parser = subparsers.add_parser("dataset-gold-import", help="将用户本地准备的数据集金标转换为 GoldQuery JSONL")  # 创建完全离线数据集适配命令。
    dataset_parser.add_argument("--input", type=Path, required=True, help="用户已准备的 dataset-gold-v1 JSONL 路径")  # 只读取用户明确指定的本地输入。
    dataset_parser.add_argument("--dataset", required=True, help="人工确认的数据集标识，例如 pasa")  # 禁止从文件名或网络推断数据集。
    dataset_parser.add_argument("--split", required=True, help="人工确认的切分标识，例如 dev-small")  # 禁止随机抽样或猜测切分。
    dataset_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的 GoldQuery JSONL 路径")  # 禁止覆盖已经人工审阅的金标。
    pasa_parser = subparsers.add_parser("pasa-gold-import", help="将已下载的 PaSa AutoScholarQuery JSONL 转换为 GoldQuery")  # 创建仅支持已确认字段版本的本地 PaSa 导入命令。
    pasa_parser.add_argument("--input", type=Path, required=True, help="用户已下载的 PaSa AutoScholarQuery 或同字段版本 JSONL 路径")  # 只读取用户明确指定的本地原始数据。
    pasa_parser.add_argument("--split", required=True, choices=["auto-dev"], help="当前已确认字段版本的 PaSa 数据切分")  # 未确认 RealScholarQuery 字段前只允许本地已验证的 AutoScholarQuery 开发集。
    pasa_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的 GoldQuery JSONL 路径")  # 禁止覆盖已经审阅的 PaSa 转换结果。
    export_parser = subparsers.add_parser("snapshot-export", help="显式授权一次候选生成并导出排序前快照")  # 创建唯一受控在线入口。
    export_parser.add_argument("--query-intent", type=Path, required=True, help="已准备好的单轮 QueryIntent JSON 路径")  # 禁止隐式调用 Query Agent。
    export_parser.add_argument("--query-id", required=True, help="评测数据集中的稳定查询标识")  # 要求显式关联评测查询。
    export_parser.add_argument("--snapshot-id", required=True, help="本次候选快照的唯一标识")  # 要求显式指定复用键。
    export_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的候选快照 JSONL 路径")  # 禁止覆盖已有在线候选。
    export_parser.add_argument("--allow-online-sources", action="store_true", help="确认允许本次命令调用真实学术来源")  # 使用单独开关形成明确在线授权。
    return parser  # 返回可测试解析器。


def main(argv: list[str] | None = None, *, candidate_service_factory: Callable[[], Any] | None = None) -> int:
    """运行指定命令并返回进程退出码；测试可注入零网络候选服务。"""
    parser = build_parser()  # 保留解析器以输出统一的授权错误。
    args = parser.parse_args(argv)  # 解析调用参数。
    if args.command == "fixture":  # 第一阶段离线评分命令。
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
    if args.command == "dataset-gold-import":  # 第四阶段完全离线数据集金标转换入口。
        gold_queries = import_prepared_dataset_gold(args.input, dataset_id=args.dataset, split=args.split, output_path=args.output)  # 只转换用户本地已准备数据，不下载或调用任何服务。
        print(f"[OK] 数据集金标已转换：{len(gold_queries)} 条查询，学术 API=0，LLM=0，本地模型=0")  # 输出不含查询正文和论文内容的安全摘要。
        return 0  # 表示零网络导入成功。
    if args.command == "pasa-gold-import":  # 第五阶段已确认 PaSa 原始格式的完全离线转换入口。
        gold_queries = import_pasa_gold(args.input, split=args.split, output_path=args.output)  # 只读取用户已下载的 PaSa 文件，不访问网络或补全论文元数据。
        print(f"[OK] PaSa 金标已转换：{len(gold_queries)} 条查询，学术 API=0，LLM=0，本地模型=0")  # 输出不含 PaSa 查询或论文正文的安全摘要。
        return 0  # 表示本地 PaSa 导入成功。
    if args.command == "snapshot-export":  # 第三阶段唯一受控在线候选生成入口。
        if not args.allow_online_sources:  # 未显式授权时不得读取配置或构造生产适配器。
            parser.error("snapshot-export 必须显式提供 --allow-online-sources；该命令可能调用真实学术 API")  # 以标准 CLI 错误拒绝隐式在线执行。
        from evaluation.runners.snapshot_export import export_candidate_snapshot_to_file, load_query_intent, validate_snapshot_export_request  # 延迟导入在线边界，保持其他命令不触碰生产服务。

        query = load_query_intent(args.query_intent)  # 只读取用户显式提供的结构化查询文件。
        validate_snapshot_export_request(query, query_id=args.query_id, snapshot_id=args.snapshot_id, output_path=args.output)  # 在创建来源客户端前完成全部静态预检。
        if candidate_service_factory is None:  # 正常 CLI 执行才装配生产候选服务。
            from backend.app.api.routes.search import get_candidate_generation_service  # 延迟读取生产配置和来源适配器工厂。

            candidate_service_factory = get_candidate_generation_service  # 复用生产候选生成装配但不进入完整搜索流程。
        generator = candidate_service_factory()  # 授权且预检成功后才创建候选服务。
        snapshot = asyncio.run(export_candidate_snapshot_to_file(generator, query, query_id=args.query_id, snapshot_id=args.snapshot_id, output_path=args.output))  # 仅执行一次规则过滤前后的候选生成闭环。
        print(f"[OK] 候选快照已封存：{snapshot.ranking_candidate_count} 篇，逻辑学术 API={snapshot.usage.academic_api_calls}，SHA-256={snapshot.snapshot_hash}")  # 输出不含查询和论文正文的安全摘要。
        return 0  # 表示快照写入成功。
    raise ValueError(f"不支持的命令: {args.command}")  # 防止未来分支静默忽略。
