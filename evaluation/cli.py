"""提供默认离线、仅经显式授权才可在线生成候选快照的评测命令。"""

import argparse  # 解析明确的本地输入与输出参数。
import asyncio  # 仅在候选快照导出分支运行异步生产候选服务。
from collections.abc import Callable  # 为测试注入不访问真实来源的候选服务工厂。
from pathlib import Path  # 规范化用户传入路径。
from typing import Any  # 避免离线命令导入生产候选服务类型。

from evaluation.runners.dataset_import import import_prepared_dataset_gold  # 转换用户本地准备的数据集金标而不下载原始数据。
from evaluation.runners.fixture import run_fixture  # 调用完全离线运行入口。
from evaluation.runners.offline_ranking import build_ablation_plan, load_ablation_matrix, write_ablation_plan  # 生成不执行模型的消融计划。
from evaluation.runners.offline_execution import execute_ablation_to_files, execute_deepseek_ablation_to_files  # 执行用户显式授权的本地或 DeepSeek 排序并原子归档。
from evaluation.runners.ablation_scoring import score_ablation_results  # 将已归档结果完全离线地分组评分并生成报告。
from evaluation.runners.coverage_diagnostic import diagnose_candidate_coverage  # 比较金标与共享候选快照的身份覆盖而不重新排序。
from evaluation.runners.query_agent_planning import EvaluationQueryPlanner, plan_query_intents_to_files, validate_query_agent_request  # 提供用户显式授权的评测 Query Agent 入口。
from evaluation.runners.pasa_import import import_pasa_gold  # 将用户已下载的确认版 PaSa 原始 JSONL 转换为统一金标。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 只读校验候选快照。
from evaluation.runners.gold_subset import select_gold_subset_to_files  # 从完整本地 GoldQuery 封存可复现的开发集子集。
from evaluation.runners.snapshot_collection import assemble_candidate_snapshot_collection, parse_snapshot_overrides  # 按冻结顺序组装多份单查询快照而不访问外部资源。
from evaluation.runners.usage_forecast import forecast_query_agent, forecast_snapshot_export, forecast_deepseek_ablation, validate_approved_forecast, validate_deepseek_ablation_forecast  # 在真实调用前生成并核验只读资源预估。
from evaluation.runners.end_to_end import execute_online_plan, score_end_to_end, write_execution_plan  # 提供固定 PaSa 端到端计划、用户显式在线执行和完全离线报告。
from evaluation.runners.longeval_audit import DEFAULT_OUTPUT_DIR as LONGEVAL_AUDIT_DEFAULT_OUTPUT_DIR, DEFAULT_RAW_ROOT as LONGEVAL_AUDIT_DEFAULT_RAW_ROOT, audit_longeval_dataset  # 只读审计已下载 LongEval 数据，不调用外部资源。
from evaluation.runners.longeval_import import DEFAULT_OUTPUT_DIR as LONGEVAL_GOLD_DEFAULT_OUTPUT_DIR, import_longeval_gold  # 将审计过的 LongEval 本地文件转换为 DOI-strict Gold，不访问外部资源。


def build_parser() -> argparse.ArgumentParser:
    """构建默认离线并隔离唯一受控在线入口的参数解析器。"""
    parser = argparse.ArgumentParser(description="ScholarFlow 离线评测与受控候选快照工具")  # 创建根命令。
    subparsers = parser.add_subparsers(dest="command", required=True)  # 明确区分离线命令和受控在线命令。
    fixture_parser = subparsers.add_parser("fixture", help="读取本地 JSONL fixture 并生成报告")  # 创建离线 fixture 命令。
    fixture_parser.add_argument("--gold", type=Path, required=True, help="本地金标 JSONL 路径")  # 要求显式金标文件。
    fixture_parser.add_argument("--predictions", type=Path, required=True, help="本地预测 JSONL 路径")  # 要求显式预测文件。
    fixture_parser.add_argument("--output-dir", type=Path, required=True, help="本地报告输出目录")  # 要求显式输出目录。
    fixture_parser.add_argument("--config", type=Path, default=None, help="可选的本地评测 JSON 配置")  # 允许调整 Top-K 和代理阈值。
    longeval_audit_parser = subparsers.add_parser("longeval-audit", help="只读审计本地 LongEval queries、qrels、documents 与 DOI 覆盖")  # 创建零网络、零模型 Phase 0 入口。
    longeval_audit_parser.add_argument("--raw-root", type=Path, default=LONGEVAL_AUDIT_DEFAULT_RAW_ROOT, help="LongEval 已解压根目录")  # 只读取用户已经解压的数据。
    longeval_audit_parser.add_argument("--output-dir", type=Path, default=LONGEVAL_AUDIT_DEFAULT_OUTPUT_DIR, help="必须不存在的审计输出目录")  # 输出 JSON、JSONL 与 Markdown。
    longeval_gold_parser = subparsers.add_parser("longeval-gold-import", help="从已审计 LongEval raw 生成 DOI-strict Gold、evidence 与 excluded ledger")  # 创建严格离线、不可覆盖的 LongEval 金标导入入口。
    longeval_gold_parser.add_argument("--raw-root", type=Path, default=LONGEVAL_AUDIT_DEFAULT_RAW_ROOT, help="已由 longeval-audit 审阅的 LongEval 解压根目录")  # 导入时重新哈希，禁止使用变更后的 raw。
    longeval_gold_parser.add_argument("--audit-dir", type=Path, default=LONGEVAL_AUDIT_DEFAULT_OUTPUT_DIR, help="已完成 longeval-audit 的报告目录")  # 要求输入哈希与 schema 已被审计冻结。
    longeval_gold_parser.add_argument("--output-dir", type=Path, default=LONGEVAL_GOLD_DEFAULT_OUTPUT_DIR, help="必须不存在的 DOI Gold 输出目录")  # 一次发布三份 split Gold、evidence、excluded 与 manifest。
    end_to_end_plan_parser = subparsers.add_parser("pasa-end-to-end-plan", help="仅生成固定 PaSa 20 条自然语言端到端执行计划")  # 创建零网络、零模型的固定集合计划入口。
    end_to_end_plan_parser.add_argument("--gold", type=Path, required=True, help="固定 PaSa 20 条 GoldQuery JSONL 路径")  # 要求显式的已封存金标。
    end_to_end_plan_parser.add_argument("--manifest", type=Path, required=True, help="固定 PaSa 20 条 subset manifest 路径")  # 要求核验封存 query_id 顺序。
    end_to_end_plan_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的在线执行计划 JSONL 路径")  # 禁止覆盖已审阅计划。
    end_to_end_execute_parser = subparsers.add_parser("pasa-end-to-end-execute", help="用户显式授权后按固定计划调用本地自然语言搜索入口")  # 创建唯一端到端在线执行入口。
    end_to_end_execute_parser.add_argument("--plan", type=Path, required=True, help="由 pasa-end-to-end-plan 生成并审阅的固定20条计划 JSONL")  # 禁止使用 QueryIntent 或候选快照替代。
    end_to_end_execute_parser.add_argument("--gold", type=Path, required=True, help="固定 PaSa 20 条 GoldQuery JSONL 路径，用于执行前再次核验")  # 防止被修改的计划改变固定分母。
    end_to_end_execute_parser.add_argument("--manifest", type=Path, required=True, help="固定 PaSa 20 条 subset manifest 路径，用于执行前再次核验")  # 防止替换或删除查询。
    end_to_end_execute_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的在线运行归档 JSONL 路径")  # 保存二十条成功和失败记录。
    end_to_end_execute_parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="用户手动启动的 ScholarFlow 后端地址")  # 仅连接用户明确运行的本地服务。
    end_to_end_execute_parser.add_argument("--timeout-seconds", type=float, default=180.0, help="单查询自然语言端到端请求超时秒数")  # 超时仍会写入固定分母记录。
    end_to_end_execute_parser.add_argument("--allow-online-end-to-end", action="store_true", help="确认允许本次命令调用真实自然语言搜索、学术来源与 LLM")  # 对真实完整链路使用独立显式授权。
    end_to_end_score_parser = subparsers.add_parser("pasa-end-to-end-score", help="完全离线评分固定 PaSa 20 条在线归档并生成报告")  # 创建不访问服务、不加载模型的评分入口。
    end_to_end_score_parser.add_argument("--gold", type=Path, required=True, help="固定 PaSa 20 条 GoldQuery JSONL 路径")  # 使用相同封存金标。
    end_to_end_score_parser.add_argument("--manifest", type=Path, required=True, help="固定 PaSa 20 条 subset manifest 路径")  # 重新校验二十条分母。
    end_to_end_score_parser.add_argument("--runs", type=Path, required=True, help="pasa-end-to-end-execute 写出的运行归档 JSONL")  # 只读取已有在线结果。
    end_to_end_score_parser.add_argument("--output-dir", type=Path, required=True, help="本地 JSON、JSONL、Markdown 报告目录")  # 写出三类用户要求的离线产物。
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
    execution_parser.add_argument("--experiment", action="append", required=True, help="可重复的矩阵 experiment_id；支持 A、B、C、D")  # 要求用户明确选择本次任务子集。
    execution_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的 OfflineAblationResult JSONL 路径")  # 禁止覆盖历史模型结果。
    execution_parser.add_argument("--manifest", type=Path, required=True, help="必须尚不存在的离线执行 manifest JSON 路径")  # 同时冻结输入与结果哈希。
    execution_parser.add_argument("--allow-local-models", action="store_true", help="确认允许本次命令加载用户提供的本地模型")  # 对真实模型加载使用单独显式授权。
    execution_parser.add_argument("--bge-model-path", type=Path, default=None, help="用户已准备且含 config.json 的本地 BGE-M3 目录")  # 不接受远程仓库名。
    execution_parser.add_argument("--bge-device", choices=["cpu", "cuda"], default="cpu", help="实际 BGE-M3 本地推理设备")  # 保证结果设备字段明确。
    execution_parser.add_argument("--bge-batch-size", type=int, default=8, help="BGE-M3 首轮本地文档编码批大小")  # 允许用户按硬件调整而不改变候选快照。
    execution_parser.add_argument("--cross-encoder-model-path", type=Path, default=None, help="用户已准备且含 config.json 的本地 Cross Encoder 目录")  # 不接受远程仓库名。
    execution_parser.add_argument("--cross-encoder-device", choices=["cpu", "cuda"], default="cpu", help="实际 Cross Encoder 本地推理设备")  # 保证阶段设备明确。
    execution_parser.add_argument("--cross-encoder-batch-size", type=int, default=8, help="Cross Encoder 本地推理批大小")  # 允许用户按硬件调整。
    execution_parser.add_argument("--allow-deepseek", action="store_true", help="确认允许本次已启用 DeepSeek 的实验调用真实 LLM")  # 与本地模型授权分离。
    execution_parser.add_argument("--forecast", type=Path, default=None, help="启用 DeepSeek 时必须提供已审阅的调用前预估 JSON")  # 预留强制预估确认入口。
    execution_parser.add_argument("--confirm-forecast", default=None, help="启用 DeepSeek 时必须提供预估中的 confirmation_sha256")  # 预留用户显式确认值。
    score_parser = subparsers.add_parser("ablation-score", help="将已归档离线结果转为预测并按实验生成评分报告")  # 创建不加载模型的结果评分入口。
    score_parser.add_argument("--results", type=Path, required=True, help="已有 OfflineAblationResult JSONL 路径")  # 只读加载用户已执行的归档结果。
    score_parser.add_argument("--run-manifest", type=Path, required=True, help="与结果配套的 offline-ranking-run manifest 路径")  # 强制核验结果字节哈希。
    score_parser.add_argument("--gold", type=Path, required=True, help="已封存 GoldQuery JSONL 路径")  # 只读加载评分金标。
    score_parser.add_argument("--config", type=Path, default=None, help="可选评测 Top-K 与代理分配置 JSON 路径")  # 只影响离线评分口径。
    score_parser.add_argument("--output-dir", type=Path, required=True, help="必须尚不存在的实验预测与报告目录")  # 禁止覆盖已经审阅的报告。
    coverage_parser = subparsers.add_parser("coverage-diagnose", help="只读诊断金标与排序前候选快照的身份覆盖")  # 创建零 API、零模型的零命中定位入口。
    coverage_parser.add_argument("--gold", type=Path, required=True, help="已封存 GoldQuery JSONL 路径")  # 使用评分相同的金标输入。
    coverage_parser.add_argument("--snapshots", type=Path, required=True, help="已封存共享 CandidateSnapshot JSONL 路径")  # 只读取 BGE-M3 前快照。
    coverage_parser.add_argument("--query-id", action="append", default=[], help="可重复的局部诊断查询标识；省略时要求 Gold 与快照完整集合相同")  # 支持独立审计一个新查询策略快照而不伪造混合集合。
    coverage_parser.add_argument("--output-dir", type=Path, required=True, help="必须尚不存在的候选覆盖诊断目录")  # 禁止覆盖已审阅诊断。
    forecast_parser = subparsers.add_parser("usage-forecast", help="只读预估下一次 Query Agent 或候选快照调用的资源上限")  # 创建不访问网络的调用前预检入口。
    forecast_parser.add_argument("--operation", choices=["query-agent-plan", "snapshot-export", "ablation-deepseek"], required=True, help="待预估的真实调用类型")  # 明确三类外部调用的不同计算口径。
    forecast_parser.add_argument("--input", type=Path, required=True, help="QueryIntent manifest 或单个 QueryIntent 路径")  # 只读取显式本地输入。
    forecast_parser.add_argument("--query-id", action="append", default=[], help="Query Agent 或单快照预估使用的稳定查询标识")  # DeepSeek 消融预估不需要单查询标识。
    forecast_parser.add_argument("--snapshot-id", default=None, help="快照导出预估必须提供的稳定快照标识")  # 防止预估被误用于其他快照。
    forecast_parser.add_argument("--matrix", type=Path, default=None, help="DeepSeek 消融预估所需矩阵 JSON 路径")  # 只读加载已审核配置。
    forecast_parser.add_argument("--plan", type=Path, default=None, help="DeepSeek 消融预估所需 ablation-plan JSON 路径")  # 绑定预估与已审核任务计划。
    forecast_parser.add_argument("--experiment", action="append", default=[], help="DeepSeek 消融预估选择的 experiment_id")  # 只统计实际启用 LLM 的实验。
    forecast_parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的预估 JSON 路径")  # 冻结用户审阅过的调用前证据。
    query_agent_parser = subparsers.add_parser("query-agent-plan", help="仅经显式授权，使用 Query Agent 从既有 QueryIntent 生成新的评测检索表达式")  # 创建受控 LLM 查询规划入口。
    query_agent_parser.add_argument("--input-manifest", type=Path, required=True, help="只包含 QueryIntent 文件映射的 query-intent-manifest-v1")  # 禁止传入 Gold、候选或报告文件。
    query_agent_parser.add_argument("--query-id", action="append", required=True, help="可重复的待规划 query_id；每个 ID 仅调用一次 Query Agent")  # 强制用户控制本次 LLM 范围。
    query_agent_parser.add_argument("--output-dir", type=Path, required=True, help="必须尚不存在的本次 QueryIntent 输出目录")  # 禁止覆盖已审阅规划。
    query_agent_parser.add_argument("--manifest", type=Path, required=True, help="必须尚不存在的 Query Agent 审计 manifest JSON 路径")  # 冻结输入、输出、Token 与费用。
    query_agent_parser.add_argument("--allow-query-agent", action="store_true", help="确认允许本次命令调用真实 Query Agent LLM")  # 使用独立开关形成明确 LLM 授权。
    query_agent_parser.add_argument("--forecast", type=Path, required=True, help="已审阅且尚与当前输入匹配的 usage-forecast JSON")  # 强制先生成调用前预估。
    query_agent_parser.add_argument("--confirm-forecast", required=True, help="用户从预估 JSON 复制的 confirmation_sha256")  # 强制用户显式确认本次上限。
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
    export_parser.add_argument("--forecast", type=Path, required=True, help="已审阅且尚与当前输入匹配的 usage-forecast JSON")  # 强制来源调用前已有预估。
    export_parser.add_argument("--confirm-forecast", required=True, help="用户从预估 JSON 复制的 confirmation_sha256")  # 强制用户确认来源调用上限。
    return parser  # 返回可测试解析器。


def main(argv: list[str] | None = None, *, candidate_service_factory: Callable[[], Any] | None = None, query_planner_factory: Callable[[], EvaluationQueryPlanner] | None = None) -> int:
    """运行指定命令并返回进程退出码；测试可注入零网络候选服务。"""
    parser = build_parser()  # 保留解析器以输出统一的授权错误。
    args = parser.parse_args(argv)  # 解析调用参数。
    if args.command == "fixture":  # 第一阶段离线评分命令。
        summary = run_fixture(args.gold, args.predictions, args.output_dir, args.config)  # 只读取本地文件并写本地报告。
        print(f"[OK] 离线评测完成：{summary.retrieval.query_count} 条查询，报告目录 {args.output_dir}")  # 输出不含查询正文的安全摘要。
        return 0  # 表示运行成功。
    if args.command == "longeval-audit":  # 完整扫描用户已经下载的 LongEval 原始数据。
        summary = audit_longeval_dataset(raw_root=args.raw_root, output_dir=args.output_dir)  # 严格只读 raw，不导入网络客户端、模型或生产服务。
        split_summary = ", ".join(f"{item.split}={item.doi_eligible_query_count}/{item.query_count}" for item in summary.splits)  # 只输出资格数量。
        print(f"[OK] LongEval 数据审计完成：Query={summary.total_query_count}，DOI-eligible={summary.total_doi_eligible_query_count}，{split_summary}，学术 API=0，LLM=0，本地模型=0")  # 明确资源边界。
        return 0  # 表示审计输出完整发布。
    if args.command == "longeval-gold-import":  # 仅将审计过的本地事实转为 DOI-strict 金标，不触碰来源或模型。
        manifest = import_longeval_gold(raw_root=args.raw_root, audit_dir=args.audit_dir, output_dir=args.output_dir)  # 导入器重新核验 raw 哈希并原子发布全部证据文件。
        split_summary = ", ".join(f"{split}={manifest.gold_query_count_by_split[split]}" for split in ("train", "heldout", "future"))  # 输出三个可评分 split 的实际分母。
        print(f"[OK] LongEval DOI Gold 导入完成：{split_summary}，排除={sum(manifest.excluded_query_count_by_split.values())}，学术 API=0，LLM=0，本地模型=0")  # 明确导入不触发任何外部资源。
        return 0  # 表示 Gold、evidence、excluded 与 manifest 已完整发布。
    if args.command == "pasa-end-to-end-plan":  # 本次任务的零网络固定20条计划生成入口。
        count = write_execution_plan(args.gold, args.manifest, args.output)  # 只核验本地 Gold 与 manifest 并写计划。
        print(f"[OK] 固定 PaSa 端到端计划已生成：{count} 条，学术 API=0，LLM=0，本地模型=0")  # 明确该步骤不触发真实运行。
        return 0  # 表示计划可供用户审阅。
    if args.command == "pasa-end-to-end-execute":  # 用户显式授权的完整自然语言在线执行入口。
        if not args.allow_online_end_to_end:  # 未授权时绝不连接本地服务或触发其下游来源、LLM。
            parser.error("pasa-end-to-end-execute 必须显式提供 --allow-online-end-to-end；该命令会调用真实自然语言搜索、学术来源与 LLM")  # 保持用户对成本和网络调用的控制。
        count = execute_online_plan(args.plan, args.output, gold_path=args.gold, manifest_path=args.manifest, base_url=args.base_url, timeout_seconds=args.timeout_seconds)  # 按固定计划顺序执行并保留全部失败记录。
        print(f"[OK] 固定 PaSa 端到端在线归档完成：{count} 条；请继续运行 pasa-end-to-end-score，在线调用仅由本次用户命令触发")  # 不回显查询正文或论文内容。
        return 0  # 表示在线归档文件已写出。
    if args.command == "pasa-end-to-end-score":  # 本次任务的完全离线评分与报告生成入口。
        summary = score_end_to_end(args.gold, args.manifest, args.runs, args.output_dir)  # 只读取本地归档，复用既有检索指标。
        print(f"[OK] 固定 PaSa 端到端离线评分完成：{summary.query_count} 条，学术 API=0，LLM=0，本地模型=0，报告目录 {args.output_dir}")  # 明确报告阶段无真实调用。
        return 0  # 表示 JSON、JSONL 和 Markdown 已生成。
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
        requires_deepseek = any(experiment.ranking_config.deepseek_enabled for experiment in selected)  # 判断是否会实际调用异步 LLM 核验。
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
        if requires_deepseek and (not args.allow_deepseek or args.forecast is None or not args.confirm_forecast):  # LLM 真实调用必须同时具备授权和预估确认。
            parser.error("启用 DeepSeek 的 ablation-execute 必须提供 --allow-deepseek、--forecast 和 --confirm-forecast")  # 在装配生产客户端前拒绝。
        if not requires_deepseek and (args.allow_deepseek or args.forecast is not None or args.confirm_forecast is not None):  # 非 LLM 实验不得伪造 DeepSeek 授权记录。
            parser.error("未选择 DeepSeek 实验时不得提供 --allow-deepseek、--forecast 或 --confirm-forecast")  # 保持调用审计与实际阶段一致。
        semantic_scorer = None  # 仅在本次实际选择 BGE-M3 时创建本地评分器。
        if requires_bge:  # 所有静态授权和路径条件通过后才延迟导入评分适配器。
            from evaluation.adapters.bge_m3 import BgeM3OfflineScorer  # 保持其他 CLI 命令不触碰模型库。

            semantic_scorer = BgeM3OfflineScorer(args.bge_model_path, device=args.bge_device, batch_size=args.bge_batch_size)  # 构造期只校验本地目录，不加载模型。
        cross_encoder_scorer = None  # 仅在本次选择 C/D 时创建本地重排器。
        if requires_cross:
            from evaluation.adapters.cross_encoder import CrossEncoderOfflineScorer  # 保持其他命令不触碰本地模型库。

            cross_encoder_scorer = CrossEncoderOfflineScorer(args.cross_encoder_model_path, device=args.cross_encoder_device, batch_size=args.cross_encoder_batch_size)  # 构造期只校验目录，不加载模型。
        if requires_deepseek:  # 只有所有授权参数通过后才延迟装配生产 DeepSeek 核验器。
            validate_deepseek_ablation_forecast(forecast_path=args.forecast, confirmation_sha256=args.confirm_forecast, snapshots_path=args.snapshots, matrix_path=args.matrix, plan_path=args.plan, experiment_ids=args.experiment)  # 在导入生产配置前核验预估。
            from backend.app.services.llm_ranking import LlmPaperReranker  # 延迟导入避免纯本地实验读取 DeepSeek 配置。
            from evaluation.adapters.deepseek import DeepSeekOfflineReranker  # 将生产核验器限制在封存快照边界。

            deepseek_reranker = DeepSeekOfflineReranker(LlmPaperReranker())  # 构造期不发请求，实际调用由异步归档器执行。
            manifest = asyncio.run(execute_deepseek_ablation_to_files(run_id=args.run_id, snapshots_path=args.snapshots, matrix_path=args.matrix, plan_path=args.plan, experiment_ids=args.experiment, output_path=args.output, manifest_path=args.manifest, deepseek_reranker=deepseek_reranker, forecast_sha256=args.confirm_forecast, semantic_scorer=semantic_scorer, cross_encoder_scorer=cross_encoder_scorer))  # 只在用户明确授权后执行异步 LLM 阶段。
        else:
            manifest = execute_ablation_to_files(run_id=args.run_id, snapshots_path=args.snapshots, matrix_path=args.matrix, plan_path=args.plan, experiment_ids=args.experiment, output_path=args.output, manifest_path=args.manifest, semantic_scorer=semantic_scorer, cross_encoder_scorer=cross_encoder_scorer)  # 只执行已计划快照上的明确本地实验。
        print(f"[OK] 离线排序结果已归档：{manifest.task_count} 个任务，学术 API=0，DeepSeek={manifest.deepseek_calls}，本地阶段={','.join(manifest.local_model_stages) or 'none'}")  # 输出归档的真实 LLM 批次调用数，不含查询正文、模型路径或论文内容。
        return 0  # 表示结果与 manifest 均已原子发布。
    if args.command == "ablation-score":  # 对既有归档结果进行完全离线的实验分组评分。
        score_manifest = score_ablation_results(results_path=args.results, run_manifest_path=args.run_manifest, gold_path=args.gold, config_path=args.config, output_dir=args.output_dir)  # 不加载本地模型或调用任何在线资源。
        print(f"[OK] 离线消融评分完成：{len(score_manifest['experiment_ids'])} 组实验，学术 API=0，DeepSeek=0，本地模型=0")  # 明确评分阶段不会重新执行模型。
        return 0  # 表示各组预测、报告和评分 manifest 已原子发布。
    if args.command == "coverage-diagnose":  # 在任何新在线候选或 DeepSeek 比较前定位零命中边界。
        summary = diagnose_candidate_coverage(gold_path=args.gold, snapshots_path=args.snapshots, output_dir=args.output_dir, query_ids=args.query_id)  # 只比较完整集合或显式局部范围的本地输入。
        print(f"[OK] 候选覆盖诊断完成：{summary.query_count} 条查询，零命中查询={summary.zero_match_query_count}，学术 API=0，DeepSeek=0，本地模型=0")  # 明确本命令不新增候选或排序。
        return 0  # 表示三份诊断文件均已发布。
    if args.command == "usage-forecast":  # 所有真实调用前的完全离线预估入口。
        if args.operation == "query-agent-plan":  # Query Agent 使用 manifest 与多个查询标识。
            if not args.query_id:
                parser.error("query-agent-plan 预估必须提供至少一个 --query-id")  # 不猜测 LLM 调用范围。
            forecast = forecast_query_agent(input_manifest_path=args.input, query_ids=args.query_id, output_path=args.output)  # 不导入 DeepSeek 客户端或读取 .env。
        elif args.operation == "snapshot-export":  # 候选快照只接受一条 QueryIntent 与一个快照标识。
            if len(args.query_id) != 1 or not args.snapshot_id:  # 不允许不明确的单快照调用预估。
                parser.error("snapshot-export 预估必须恰好提供一个 --query-id 和 --snapshot-id")  # 在读取输入前拒绝歧义范围。
            forecast = forecast_snapshot_export(query_intent_path=args.input, query_id=args.query_id[0], snapshot_id=args.snapshot_id, output_path=args.output)  # 不创建学术来源客户端。
        else:  # DeepSeek 消融预估以 input 作为封存候选集合路径。
            if args.matrix is None or args.plan is None or not args.experiment:  # 不允许按默认或隐式矩阵猜测调用范围。
                parser.error("ablation-deepseek 预估必须提供 --matrix、--plan 和 --experiment")  # 在读取模型或网络前拒绝。
            forecast = forecast_deepseek_ablation(snapshots_path=args.input, matrix_path=args.matrix, plan_path=args.plan, experiment_ids=args.experiment, output_path=args.output)  # 只读生成 LLM 上限预估。
        print(f"[OK] 调用前预估已生成：DeepSeek={forecast['deepseek_calls']}，学术 API={forecast['academic_api_calls']}，确认 SHA-256={forecast['confirmation_sha256']}")  # 输出用户下一步确认所需哈希而不回显查询正文。
        return 0  # 表示仅完成本地预估。
    if args.command == "query-agent-plan":  # 由用户显式授权的评测检索表达式生成，不进入候选或排序流程。
        if not args.allow_query_agent:  # 未授权时连输入 manifest 也不读取，避免隐藏 LLM 意图。
            parser.error("query-agent-plan 必须显式提供 --allow-query-agent；该命令会调用真实 Query Agent LLM")  # 以标准 CLI 错误拒绝隐式模型调用。
        validate_query_agent_request(input_manifest_path=args.input_manifest, query_ids=args.query_id, output_dir=args.output_dir, manifest_path=args.manifest)  # 在导入生产配置和读取 .env 前完成静态预检。
        validate_approved_forecast(forecast_path=args.forecast, confirmation_sha256=args.confirm_forecast, operation="query-agent-plan", input_path=args.input_manifest, query_ids=args.query_id)  # 预估不匹配时不得装配 DeepSeek 客户端。
        if query_planner_factory is None:  # 正常 CLI 仅在用户显式授权后才装配生产 Query Agent。
            from backend.app.services.query_planning import QueryPlanningService  # 延迟导入会读取 DeepSeek 配置的生产服务。

            query_planner_factory = QueryPlanningService  # 复用已验证的 Query Agent 适配器而不修改生产 API。
        audit = asyncio.run(plan_query_intents_to_files(planner=query_planner_factory(), input_manifest_path=args.input_manifest, query_ids=args.query_id, output_dir=args.output_dir, manifest_path=args.manifest))  # 只输入原问题和显式条件，不读取 Gold 或候选。
        print(f"[OK] Query Agent 评测规划完成：{len(audit['query_id_order'])} 条，学术 API=0，DeepSeek={audit['deepseek_calls']}，本地模型=0")  # 输出安全成本摘要而不回显查询正文。
        return 0  # 表示新的 QueryIntent 与审计 manifest 均已写出。
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
        validate_approved_forecast(forecast_path=args.forecast, confirmation_sha256=args.confirm_forecast, operation="snapshot-export", input_path=args.query_intent, query_ids=[args.query_id], snapshot_id=args.snapshot_id)  # 预估不匹配时不得创建来源客户端。
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
