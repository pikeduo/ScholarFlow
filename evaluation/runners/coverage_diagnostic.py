"""生成不访问网络、模型或报告目录的候选覆盖诊断。"""

import hashlib  # 计算用户显式输入文件的原始字节 SHA-256。
import json  # 写入 UTF-8 JSON、JSONL 和 Markdown 审计输出。
import os  # 原子发布完整诊断目录。
import shutil  # 仅清理本模块创建但未发布的临时目录。
from pathlib import Path  # 处理用户显式指定的本地文件与目录。
from tempfile import mkdtemp  # 在正式目录旁创建同文件系统临时目录。

from evaluation.contracts.common import EvaluationPaper  # 统一比较金标论文和候选论文。
from evaluation.contracts.coverage_diagnostic import CoverageDiagnosticSummary, QueryCoverageDiagnostic  # 复用稳定输出契约。
from evaluation.contracts.gold import GoldQuery  # 读取已封存的金标查询。
from evaluation.metrics.identifiers import IDENTIFIER_FIELDS, has_strong_identifier, normalize_internal_id, normalize_text, papers_match  # 与正式指标复用同一身份规则。
from evaluation.runners.fixture import load_jsonl  # 使用既有 UTF-8 JSONL 契约加载器。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 校验共享快照的哈希和去重承诺。


def diagnose_candidate_coverage(*, gold_path: Path, snapshots_path: Path, output_dir: Path) -> CoverageDiagnosticSummary:
    """比较金标与排序前候选，写出完全离线的身份覆盖诊断目录。

    参数：金标 JSONL、共享 CandidateSnapshot JSONL 和必须不存在的输出目录。
    返回：包含输入摘要和聚合计数的诊断汇总。
    异常：输入不一致、契约无效或输出已存在时抛出明确异常。
    """
    if output_dir.exists():  # 已审阅诊断不得被重写。
        raise FileExistsError(f"候选覆盖诊断输出目录已存在: {output_dir}")  # 要求调用方使用新输出路径。
    gold_queries = load_jsonl(gold_path, GoldQuery)  # 只读加载并校验金标契约。
    snapshots = load_candidate_snapshots(snapshots_path)  # 只读加载、校验哈希并复用排序前快照。
    gold_index = _index_unique(gold_queries, "gold")  # 拒绝重复金标查询标识。
    snapshot_index = _index_unique(snapshots, "候选快照")  # 拒绝重复快照查询标识。
    if set(gold_index) != set(snapshot_index):  # 覆盖诊断必须使用完全相同的查询分母。
        missing_snapshots = sorted(set(gold_index) - set(snapshot_index))  # 收集缺少候选的金标查询。
        extra_snapshots = sorted(set(snapshot_index) - set(gold_index))  # 收集没有金标的候选查询。
        raise ValueError(f"金标与候选快照 query_id 不一致: 缺少快照={','.join(missing_snapshots) or '无'}; 额外快照={','.join(extra_snapshots) or '无'}")  # 不生成部分诊断。
    diagnostics = [_diagnose_query(gold_query, snapshot_index[gold_query.query_id]) for gold_query in gold_queries]  # 严格按 GoldQuery 输入顺序生成审计记录。
    summary = CoverageDiagnosticSummary(  # 只聚合事实性计数，不产出官方或代理得分。
        gold_sha256=_sha256(gold_path),  # 冻结金标输入内容。
        snapshots_sha256=_sha256(snapshots_path),  # 冻结共享候选集合内容。
        query_count=len(diagnostics),  # 记录严格对齐后的查询数量。
        total_gold_paper_count=sum(record.gold_paper_count for record in diagnostics),  # 汇总金标分母。
        total_candidate_paper_count=sum(record.candidate_paper_count for record in diagnostics),  # 汇总排序前候选规模。
        matched_gold_paper_count=sum(record.matched_gold_paper_count for record in diagnostics),  # 汇总可由现有身份规则确认的覆盖。
        zero_match_query_count=sum(record.matched_gold_paper_count == 0 for record in diagnostics),  # 汇总零命中查询数。
        strong_identifier_gold_count=sum(record.strong_identifier_gold_count for record in diagnostics),  # 汇总金标身份可比性。
        strong_identifier_candidate_count=sum(record.strong_identifier_candidate_count for record in diagnostics),  # 汇总候选身份可比性。
        warnings=["本报告只比较已封存 GoldQuery 与排序前候选快照，不调用学术 API、LLM 或本地模型。", "零命中只能证明当前快照在 papers_match-v1 下未覆盖金标；不能单独归因于来源、查询、规范化或排序。"],  # 固定解释边界。
    )
    _write_diagnostic_directory(output_dir, summary, diagnostics)  # 全部成功后才发布完整目录。
    return summary  # 返回 CLI 所需的安全聚合摘要。


def _diagnose_query(gold_query: GoldQuery, snapshot: object) -> QueryCoverageDiagnostic:
    """对单条查询计算匹配数量、标识符可比性和事实性边界标记。"""
    papers = snapshot.papers  # CandidateSnapshot 经过加载器校验后提供排序前候选。
    matches: list[tuple[EvaluationPaper, EvaluationPaper, str]] = []  # 保存每个匹配金标与候选的最低审计信息。
    matched_gold_indexes: set[int] = set()  # 防止一个金标论文被多个候选重复计数。
    matched_candidate_indexes: set[int] = set()  # 防止一个候选被多个金标重复计数。
    for gold_index, gold_paper in enumerate(gold_query.relevant_papers):  # 逐篇金标比较当前快照全部候选。
        for candidate_index, candidate_paper in enumerate(papers):  # 保持候选快照中的稳定 RRF 顺序。
            if not papers_match(gold_paper, candidate_paper):  # 严格复用正式指标的保守身份规则。
                continue  # 不将近似标题或语义相似伪装为命中。
            matched_gold_indexes.add(gold_index)  # 当前金标已有至少一个可确认候选。
            matched_candidate_indexes.add(candidate_index)  # 当前候选已有至少一个可确认金标。
            matches.append((gold_paper, candidate_paper, _match_mode(gold_paper, candidate_paper)))  # 保存可解释的匹配类型。
    flags: list[str] = []  # 只记录可由本地输入直接观察到的标记。
    if not papers:  # 空候选与非空零命中必须区分。
        flags.append("ranking_candidates_empty")  # 表示此查询没有进入离线排序的候选。
    if not matches:  # 所有实际评分配置都无法突破候选召回边界。
        flags.append("no_gold_candidate_identity_match")  # 表示现有身份规则下没有覆盖。
    if any(not has_strong_identifier(paper) for paper in gold_query.relevant_papers):  # 标识符缺失会限制跨来源可比性。
        flags.append("gold_contains_title_fallback_records")  # 不对数据质量或来源做进一步推断。
    if any(not has_strong_identifier(paper) for paper in papers):  # 候选标识符缺失同样影响比较。
        flags.append("candidates_contain_title_fallback_records")  # 明确这是比较边界而非错误。
    return QueryCoverageDiagnostic(  # 以查询 ID 而非正文生成脱敏审计记录。
        query_id=gold_query.query_id,  # 保持 GoldQuery 稳定顺序与标识。
        snapshot_id=snapshot.snapshot_id,  # 关联不可变快照。
        gold_paper_count=len(gold_query.relevant_papers),  # 保留该查询金标规模。
        candidate_paper_count=len(papers),  # 保留该查询排序输入规模。
        matched_gold_paper_count=len(matched_gold_indexes),  # 避免一对多重复计数。
        matched_candidate_paper_count=len(matched_candidate_indexes),  # 避免多对一重复计数。
        strong_identifier_gold_count=sum(has_strong_identifier(paper) for paper in gold_query.relevant_papers),  # 统计可强匹配的金标。
        strong_identifier_candidate_count=sum(has_strong_identifier(paper) for paper in papers),  # 统计可强匹配的候选。
        strong_identifier_match_count=sum(mode == "strong_identifier" for _, _, mode in matches),  # 统计强标识确认对。
        internal_identifier_match_count=sum(mode == "internal_identifier" for _, _, mode in matches),  # 统计内部标识确认对。
        title_fallback_match_count=sum(mode == "title_fallback" for _, _, mode in matches),  # 统计标题回退确认对。
        diagnostic_flags=flags,  # 写出事实性标记。
    )


def _match_mode(left: EvaluationPaper, right: EvaluationPaper) -> str:
    """返回已经通过 papers_match 的论文对所使用的最强确认方式。"""
    for field_name, normalizer in IDENTIFIER_FIELDS:  # 与 papers_match 保持同一强标识符优先级。
        left_value = normalizer(getattr(left, field_name))  # 规范化左侧字段。
        right_value = normalizer(getattr(right, field_name))  # 规范化右侧字段。
        if left_value and right_value and left_value == right_value:  # 同一强标识符即为最高可信确认。
            return "strong_identifier"  # 不再检查较弱方式。
    if normalize_internal_id(left.paper_id) and normalize_internal_id(left.paper_id) == normalize_internal_id(right.paper_id):  # 只有相同内部标识才可确认。
        return "internal_identifier"  # 明确此方式不等价于跨来源强标识。
    if normalize_text(left.title) and normalize_text(left.title) == normalize_text(right.title):  # 剩余成功匹配只能来自正式标题回退规则。
        return "title_fallback"  # 记录较弱确认方式。
    raise ValueError("已匹配论文无法确定匹配方式")  # 防止匹配规则与诊断分类悄然漂移。


def _index_unique(records: list[object], label: str) -> dict[str, object]:
    """按 query_id 建立索引并拒绝会改变分母的重复记录。"""
    index: dict[str, object] = {}  # 保存输入顺序外的稳定查询查找。
    for record in records:  # 逐条检查唯一性。
        if record.query_id in index:  # 重复查询会导致覆盖归因不确定。
            raise ValueError(f"{label}存在重复 query_id: {record.query_id}")  # 不生成半可信报告。
        index[record.query_id] = record  # 保存通过唯一性检查的记录。
    return index  # 返回稳定索引。


def _sha256(path: Path) -> str:
    """返回本地输入原始字节的 SHA-256，不改写文件。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()  # 与执行归档使用相同的原始字节冻结方式。


def _write_diagnostic_directory(output_dir: Path, summary: CoverageDiagnosticSummary, diagnostics: list[QueryCoverageDiagnostic]) -> None:
    """在同一文件系统构建完整报告目录，并仅在成功后原子发布。"""
    output_dir.parent.mkdir(parents=True, exist_ok=True)  # 允许用户选择尚不存在的报告父目录。
    temporary_dir = Path(mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))  # 避免在正式路径留下半成品。
    try:  # 任何序列化或写入异常均不发布输出目录。
        (temporary_dir / "diagnostic.json").write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")  # 写入机器可读总览。
        (temporary_dir / "query_diagnostics.jsonl").write_text("".join(record.model_dump_json() + "\n" for record in diagnostics), encoding="utf-8")  # 按 GoldQuery 顺序写逐查询审计。
        (temporary_dir / "diagnostic.md").write_text(_markdown(summary), encoding="utf-8")  # 写入无需载入论文正文的人读摘要。
        os.replace(temporary_dir, output_dir)  # 仅在三个文件均成功写入后发布目录。
    except Exception:  # 失败时只删除本函数创建的临时目录。
        shutil.rmtree(temporary_dir, ignore_errors=True)  # 不触碰任何用户已有目录。
        raise  # 保留原始错误供调用方处理。


def _markdown(summary: CoverageDiagnosticSummary) -> str:
    """生成不含查询正文和论文内容的简洁 Markdown 诊断摘要。"""
    return (  # 使用固定栏目，便于人工判断下一步应先修复覆盖还是比较契约。
        "# 候选覆盖诊断\n\n"
        f"- 查询数：{summary.query_count}\n"
        f"- 金标论文数：{summary.total_gold_paper_count}\n"
        f"- 排序前候选数：{summary.total_candidate_paper_count}\n"
        f"- 已命中的金标论文数：{summary.matched_gold_paper_count}\n"
        f"- 零命中查询数：{summary.zero_match_query_count}\n"
        f"- 具强标识符的金标/候选：{summary.strong_identifier_gold_count}/{summary.strong_identifier_candidate_count}\n\n"
        "## 边界\n\n"
        "- 本报告不重新排序、不加载模型、不调用 DeepSeek 或学术 API。\n"
        "- `query_diagnostics.jsonl` 只记录查询标识和可复核计数；请先依据其结果决定是否需要调整身份映射或由用户显式重建在线候选。\n"
    )
