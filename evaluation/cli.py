"""提供默认离线、仅经显式授权才可在线生成候选快照的评测命令。"""

import argparse  # 解析明确的本地输入与输出参数。
import asyncio  # 仅在候选快照导出分支运行异步生产候选服务。
from collections.abc import Callable  # 为测试注入不访问真实来源的候选服务工厂。
from pathlib import Path  # 规范化用户传入路径。
from typing import Any  # 避免离线命令导入生产候选服务类型。

from evaluation.runners.dataset_import import import_prepared_dataset_gold  # 转换用户本地准备的数据集金标而不下载原始数据。
from evaluation.runners.fixture import run_fixture  # 调用完全离线运行入口。
from evaluation.runners.offline_ranking import build_ablation_plan, load_ablation_matrix, write_ablation_plan  # 生成不执行模型的消融计划。
from evaluation.runners.offline_execution import execute_ablation_to_files  # 执行用户显式授权的本地排序并原子归档。
from evaluation.runners.ablation_scoring import score_ablation_results  # 将已归档结果完全离线地分组评分并生成报告。
from evaluation.runners.pasa_import import import_pasa_gold  # 将用户已下载的确认版 PaSa 原始 JSONL 转换为统一金标。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 只读校验候选快照。
from evaluation.runners.gold_subset import select_gold_subset_to_files  # 从完整本地 GoldQuery 封存可复现的开发集子集。
from evaluation.runners.snapshot_collection import assemble_candidate_snapshot_collection, parse_snapshot_overrides  # 按冻结顺序组装多份单查询快照而不访问外部资源。


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
    collection_parser = subparsers.add_parser("snapshot-collection-assemble", help="离线组装按 QueryIntent manifest 冻结顺序排列的候选快照集合")  # 创建完全离线的多文件快照集合入口。
    collection_parser.add_argument("--collection-id", required=True, help="本次共享候选集合的稳定人工标识")  # 要求用户显式冻结集合用途与版本。
    collection_parser.add_argument("--query-intent-manifest", type=Path, required=True, help="已封存 QueryIntent manifest JSON 路径")  # 只读取其中冻结的 query_id 顺序和候选参数。
    collection_parser.add_argument("--snapshot-dir", type=Path, required=True, help="单查询候选快照所在目录")  # 只扫描用户显式指定目录内的 JSONL。
    collection_parser.add_argument("--snapshot-override", action="append", default=[], help="可重复的 query_id=目录内相对快照路径，用于选择多个成功重试中的唯一版本")  # 禁止脚本自行猜测重试选择。
    collection_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的集合 CandidateSnapshot JSONL 路径")  # 禁止覆盖已用于后续排序的集合。
    collection_parser.add_argument("--manifest", type=Path, required=True, help="必须尚不存在的集合审计 manifest JSON 路径")  # 要求同时封存选择映射和哈希。
    plan_parser = subparsers.add_parser("ablation-plan", help="组合本地快照和矩阵，只生成零 API、零 DeepSeek 计划")  # 创建消融计划命令。
    plan_parser.add_argument("--snapshots", type=Path, required=True, help="本地候选快照 JSONL 路径")  # 只读加载共享候选。
    plan_parser.add_argument("--matrix", type=Path, required=True, help="本地 A/B/C/D 矩阵 JSON 路径")  # 只读加载排序配置。
    plan_parser.add_argument("--output", type=Path, required=True, help="本地计划 JSON 输出路径")  # 要求显式输出文件。
    execution_parser = subparsers.add_parser("ablation-execute", help="按已有计划执行明确选择的离线排序实验并原子归档")  # 创建不访问学术 API 的本地模型执行入口。
    execution_parser.add_argument("--run-id", required=True, help="本次离线结果归档的稳定人工标识")  # 要求用户显式标识每次模型执行。
    execution_parser.add_argument("--snapshots", type=Path, required=True, help="已封存的共享候选快照 JSONL 路径")  # 只读加载既有集合。
    execution_parser.add_argument("--matrix", type=Path, required=True, help="已审核的 A/B/C/D 矩阵 JSON 路径")  # 只读加载配置。
    execution_parser.add_argument("--plan", type=Path, required=True, help="已有 ablation-plan JSON 路径")  # 强制执行前复核计划输入。
    execution_parser.add_argument("--experiment", action="append", required=True, help="可重复的矩阵 experiment_id；当前仅支持 A、B")  # 要求用户明确选择本次任务子集。
    execution_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的 OfflineAblationResult JSONL 路径")  # 禁止覆盖历史模型结果。
    execution_parser.add_argument("--manifest", type=Path, required=True, help="必须尚不存在的离线执行 manifest JSON 路径")  # 同时冻结输入与结果哈希。
    execution_parser.add_argument("--allow-local-models", action="store_true", help="确认允许本次命令加载用户提供的本地模型")  # 对真实模型加载使用单独显式授权。
    execution_parser.add_argument("--bge-model-path", type=Path, default=None, help="用户已准备且含 config.json 的本地 BGE-M3 目录")  # 不接受远程仓库名。
    execution_parser.add_argument("--bge-device", choices=["cpu", "cuda"], default="cpu", help="实际 BGE-M3 本地推理设备")  # 保证结果设备字段明确。
    execution_parser.add_argument("--bge-batch-size", type=int, default=8, help="BGE-M3 首轮本地文档编码批大小")  # 允许用户按硬件调整而不改变候选快照。
    execution_parser.add_argument("--cross-encoder-model-path", type=Path, default=None, help="用户已准备且含 config.json 的本地 Cross Encoder 目录")  # 不接受远程仓库名。
    execution_parser.add_argument("--cross-encoder-device", choices=["cpu", "cuda"], default="cpu", help="实际 Cross Encoder 本地推理设备")  # 保证阶段设备明确。
    execution_parser.add_argument("--cross-encoder-batch-size", type=int, default=8, help="Cross Encoder 本地推理批大小")  # 允许用户按硬件调整。
    score_parser = subparsers.add_parser("ablation-score", help="将已归档离线结果转为预测并按实验生成评分报告")  # 创建不加载模型的结果评分入口。
    score_parser.add_argument("--results", type=Path, required=True, help="已有 OfflineAblationResult JSONL 路径")  # 只读加载用户已执行的归档结果。
    score_parser.add_argument("--run-manifest", type=Path, required=True, help="与结果配套的 offline-ranking-run manifest 路径")  # 强制核验结果字节哈希。
    score_parser.add_argument("--gold", type=Path, required=True, help="已封存 GoldQuery JSONL 路径")  # 只读加载评分金标。
    score_parser.add_argument("--config", type=Path, default=None, help="可选评测 Top-K 与代理分配置 JSON 路径")  # 只影响离线评分口径。
    score_parser.add_argument("--output-dir", type=Path, required=True, help="必须尚不存在的实验预测与报告目录")  # 禁止覆盖已经审阅的报告。
    dataset_parser = subparsers.add_parser("dataset-gold-import", help="将用户本地准备的数据集金标转换为 GoldQuery JSONL")  # 创建完全离线数据集适配命令。
    dataset_parser.add_argument("--input", type=Path, required=True, help="用户已准备的 dataset-gold-v1 JSONL 路径")  # 只读取用户明确指定的本地输入。
    dataset_parser.add_argument("--dataset", required=True, help="人工确认的数据集标识，例如 pasa")  # 禁止从文件名或网络推断数据集。
    dataset_parser.add_argument("--split", required=True, help="人工确认的切分标识，例如 dev-small")  # 禁止随机抽样或猜测切分。
    dataset_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的 GoldQuery JSONL 路径")  # 禁止覆盖已经人工审阅的金标。
    pasa_parser = subparsers.add_parser("pasa-gold-import", help="将已下载的 PaSa AutoScholarQuery JSONL 转换为 GoldQuery")  # 创建仅支持已确认字段版本的本地 PaSa 导入命令。
    pasa_parser.add_argument("--input", type=Path, required=True, help="用户已下载的 PaSa AutoScholarQuery 或同字段版本 JSONL 路径")  # 只读取用户明确指定的本地原始数据。
    pasa_parser.add_argument("--split", required=True, choices=["auto-dev"], help="当前已确认字段版本的 PaSa 数据切分")  # 未确认 RealScholarQuery 字段前只允许本地已验证的 AutoScholarQuery 开发集。
    pasa_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的 GoldQuery JSONL 路径")  # 禁止覆盖已经审阅的 PaSa 转换结果。
    subset_parser = subparsers.add_parser("gold-subset-select", help="从本地 GoldQuery 封存可复现的开发集子集")  # 创建不读取配置和不调用服务的子集选择命令。
    subset_parser.add_argument("--input", type=Path, required=True, help="已验证的完整 GoldQuery JSONL 路径")  # 只读取用户明确指定的本地金标输入。
    subset_parser.add_argument("--count", type=int, required=True, help="本次子集查询数，例如开发集评测的 20")  # 保持开发集规模与候选和 Top-K 参数明确分离。
    subset_parser.add_argument("--selection-id", required=True, help="人工冻结的子集用途与版本标识")  # 要求用户明确区分不同实验子集。
    subset_parser.add_argument("--seed", required=True, help="参与稳定 SHA-256 排序的显式种子文本")  # 禁止隐式随机状态导致无法重现。
    subset_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的子集 GoldQuery JSONL 路径")  # 禁止覆盖已用于候选快照的开发集。
    subset_parser.add_argument("--manifest", type=Path, required=True, help="必须尚不存在的子集审计 manifest JSON 路径")  # 要求单独封存算法、哈希和完整 query_id 列表。
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
    if args.command == "snapshot-collection-assemble":  # 将用户已完成的单查询快照离线组装为共享排序输入。
        overrides = parse_snapshot_overrides(args.snapshot_override)  # 在读取目录前校验多重重试的显式选择语法。
        collection_manifest = assemble_candidate_snapshot_collection(  # 只读取本地 manifest 和快照，并写入新集合文件。
            collection_id=args.collection_id,
            query_intent_manifest_path=args.query_intent_manifest,
            snapshot_directory=args.snapshot_dir,
            snapshot_overrides=overrides,
            output_path=args.output,
            manifest_path=args.manifest,
        )
        print(f"[OK] 候选快照集合已封存：{len(collection_manifest.query_id_order)} 条，学术 API=0，LLM=0，本地模型=0")  # 明确组装不触发任何在线或模型资源。
        return 0  # 表示集合 JSONL 与 manifest 已写出。
    if args.command == "ablation-plan":  # 第二阶段生成不执行模型的本地任务计划。
        snapshots = load_candidate_snapshots(args.snapshots)  # 只读加载并核验共享候选。
        matrix = load_ablation_matrix(args.matrix)  # 加载统一来源召回和评分配置。
        plan = build_ablation_plan(snapshots, matrix)  # 组合快照和矩阵但不调用打分器。
        write_ablation_plan(plan, args.output)  # 写出用户指定计划文件。
        print(f"[OK] 离线消融计划完成：{plan.task_count} 个任务，学术 API=0，DeepSeek=0")  # 明确资源边界。
        return 0  # 表示计划生成成功。
    if args.command == "ablation-execute":  # 执行用户明确选择的计划内本地排序子集。
        matrix = load_ablation_matrix(args.matrix)  # 只读检查所选实验是否需要本地 BGE-M3。
        selected = [experiment for experiment in matrix.experiments if experiment.experiment_id in args.experiment]  # 不按命令行顺序改变矩阵的稳定顺序。
        requires_bge = any(experiment.ranking_config.semantic_ranking_enabled for experiment in selected)  # 判断是否会实际加载本地模型。
        requires_cross = any(experiment.ranking_config.cross_encoder_ranking_enabled for experiment in selected)  # 判断是否会实际加载本地重排模型。
        if (requires_bge or requires_cross) and not args.allow_local_models:  # 任一本地模型任务必须有独立显式授权。
            parser.error("ablation-execute 执行本地模型必须显式提供 --allow-local-models")  # 在构造评分器前拒绝。
        if requires_bge and args.bge_model_path is None:  # 不允许调用方遗漏已准备的本地模型目录。
            parser.error("ablation-execute 执行 BGE-M3 必须提供 --bge-model-path")  # 不下载或猜测模型位置。
        if not requires_bge and args.bge_model_path is not None:  # 基线 A 不应无意义地装配模型。
            parser.error("未选择 BGE-M3 实验时不得提供 --bge-model-path")  # 避免用户误以为模型已执行。
        if requires_cross and args.cross_encoder_model_path is None:
            parser.error("ablation-execute 执行 Cross Encoder 必须提供 --cross-encoder-model-path")  # 不下载或猜测模型位置。
        if not requires_cross and args.cross_encoder_model_path is not None:
            parser.error("未选择 Cross Encoder 实验时不得提供 --cross-encoder-model-path")  # 避免无意义模型装配。
        semantic_scorer = None  # 仅在本次实际选择 BGE-M3 时创建本地评分器。
        if requires_bge:  # 所有静态授权和路径条件通过后才延迟导入评分适配器。
            from evaluation.adapters.bge_m3 import BgeM3OfflineScorer  # 保持其他 CLI 命令不触碰模型库。

            semantic_scorer = BgeM3OfflineScorer(args.bge_model_path, device=args.bge_device, batch_size=args.bge_batch_size)  # 构造期只校验本地目录，不加载模型。
        cross_encoder_scorer = None  # 仅在本次选择 C/D 时创建本地重排器。
        if requires_cross:
            from evaluation.adapters.cross_encoder import CrossEncoderOfflineScorer  # 保持其他命令不触碰本地模型库。

            cross_encoder_scorer = CrossEncoderOfflineScorer(args.cross_encoder_model_path, device=args.cross_encoder_device, batch_size=args.cross_encoder_batch_size)  # 构造期只校验目录，不加载模型。
        manifest = execute_ablation_to_files(run_id=args.run_id, snapshots_path=args.snapshots, matrix_path=args.matrix, plan_path=args.plan, experiment_ids=args.experiment, output_path=args.output, manifest_path=args.manifest, semantic_scorer=semantic_scorer, cross_encoder_scorer=cross_encoder_scorer)  # 只执行已计划快照上的明确本地实验。
        print(f"[OK] 离线排序结果已归档：{manifest.task_count} 个任务，学术 API=0，DeepSeek=0，本地阶段={','.join(manifest.local_model_stages) or 'none'}")  # 输出不含查询正文、模型路径或论文内容的安全摘要。
        return 0  # 表示结果与 manifest 均已原子发布。
    if args.command == "ablation-score":  # 对既有归档结果进行完全离线的实验分组评分。
        score_manifest = score_ablation_results(results_path=args.results, run_manifest_path=args.run_manifest, gold_path=args.gold, config_path=args.config, output_dir=args.output_dir)  # 不加载本地模型或调用任何在线资源。
        print(f"[OK] 离线消融评分完成：{len(score_manifest['experiment_ids'])} 组实验，学术 API=0，DeepSeek=0，本地模型=0")  # 明确评分阶段不会重新执行模型。
        return 0  # 表示各组预测、报告和评分 manifest 已原子发布。
    if args.command == "dataset-gold-import":  # 第四阶段完全离线数据集金标转换入口。
        gold_queries = import_prepared_dataset_gold(args.input, dataset_id=args.dataset, split=args.split, output_path=args.output)  # 只转换用户本地已准备数据，不下载或调用任何服务。
        print(f"[OK] 数据集金标已转换：{len(gold_queries)} 条查询，学术 API=0，LLM=0，本地模型=0")  # 输出不含查询正文和论文内容的安全摘要。
        return 0  # 表示零网络导入成功。
    if args.command == "pasa-gold-import":  # 第五阶段已确认 PaSa 原始格式的完全离线转换入口。
        gold_queries = import_pasa_gold(args.input, split=args.split, output_path=args.output)  # 只读取用户已下载的 PaSa 文件，不访问网络或补全论文元数据。
        print(f"[OK] PaSa 金标已转换：{len(gold_queries)} 条查询，学术 API=0，LLM=0，本地模型=0")  # 输出不含 PaSa 查询或论文正文的安全摘要。
        return 0  # 表示本地 PaSa 导入成功。
    if args.command == "gold-subset-select":  # 第六阶段开发集 GoldQuery 子集的完全离线封存入口。
        manifest = select_gold_subset_to_files(args.input, count=args.count, selection_id=args.selection_id, selection_seed=args.seed, output_path=args.output, manifest_path=args.manifest)  # 只处理本地金标并同时封存哈希和完整 ID 列表。
        print(f"[OK] GoldQuery 子集已封存：{manifest.selected_query_count}/{manifest.source_query_count} 条，SHA-256={manifest.selected_gold_sha256}，学术 API=0，LLM=0，本地模型=0")  # 输出不含查询正文，仅提供可复核规模与哈希。
        return 0  # 表示零网络子集封存成功。
    if args.command == "snapshot-export":  # 第三阶段唯一受控在线候选生成入口。
        if not args.allow_online_sources:  # 未显式授权时不得读取配置或构造生产适配器。
            parser.error("snapshot-export 必须显式提供 --allow-online-sources；该命令可能调用真实学术 API")  # 以标准 CLI 错误拒绝隐式在线执行。
        from evaluation.runners.snapshot_export import AllAcademicSourcesFailedError, export_candidate_snapshot_to_file, load_query_intent, validate_snapshot_export_request  # 延迟导入在线边界，保持其他命令不触碰生产服务。

        query = load_query_intent(args.query_intent)  # 只读取用户显式提供的结构化查询文件。
        validate_snapshot_export_request(query, query_id=args.query_id, snapshot_id=args.snapshot_id, output_path=args.output)  # 在创建来源客户端前完成全部静态预检。
        if candidate_service_factory is None:  # 正常 CLI 执行才装配生产候选服务。
            from backend.app.api.routes.search import get_candidate_generation_service  # 延迟读取生产配置和来源适配器工厂。

            candidate_service_factory = get_candidate_generation_service  # 复用生产候选生成装配但不进入完整搜索流程。
        generator = candidate_service_factory()  # 授权且预检成功后才创建候选服务。
        try:  # 全部学术来源失败属于预期的不可封存结果边界，应以稳定退出码返回而非输出成功摘要。
            snapshot = asyncio.run(export_candidate_snapshot_to_file(generator, query, query_id=args.query_id, snapshot_id=args.snapshot_id, output_path=args.output))  # 仅执行一次规则过滤前后的候选生成闭环。
        except AllAcademicSourcesFailedError as error:  # 候选服务已完成调用，但不存在可用于离线排序的候选快照。
            print(f"[ERROR] {error}")  # 输出不含查询、密钥和供应商原始错误正文的失败摘要。
            return 1  # 让 CLI 向调用脚本报告失败，且不输出 [OK]。
        print(f"[OK] 候选快照已封存：{snapshot.ranking_candidate_count} 篇，逻辑学术 API={snapshot.usage.academic_api_calls}，SHA-256={snapshot.snapshot_hash}")  # 输出不含查询和论文正文的安全摘要。
        return 0  # 表示快照写入成功。
    raise ValueError(f"不支持的命令: {args.command}")  # 防止未来分支静默忽略。
