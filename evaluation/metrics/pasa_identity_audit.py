"""审计 PaSa 稀疏金标与生产论文记录之间的可解释身份差异。"""

from collections import Counter  # 汇总每类身份证据数量。

from evaluation.contracts.common import EvaluationPaper  # 使用统一论文契约。
from evaluation.contracts.gold import GoldQuery  # 读取 PaSa GoldQuery。
from evaluation.contracts.prediction import PredictionRecord  # 读取最终排序论文。
from backend.app.models.paper_identity import compare_strong_identifiers, normalize_text  # 复用生产与评测共享的强标识与标题规范化规则。
from evaluation.metrics.identifiers import deduplicate_papers  # 保持审计精确率分母与通用评分一致。


def _sparse_gold_title_match(gold: EvaluationPaper, predicted: EvaluationPaper) -> bool:
    """只为缺少年份和作者的 PaSa 金标启用完全标题回退。"""
    gold_title = normalize_text(gold.title)  # 规范化 PaSa 提供的金标标题。
    predicted_title = normalize_text(predicted.title)  # 规范化生产最终论文标题。
    return bool(gold_title and gold_title == predicted_title and gold.year is None and not gold.authors)  # 金标已有消歧字段时继续使用严格规则。


def _match_kind(gold: EvaluationPaper, predicted: EvaluationPaper) -> str | None:
    """按严格标识、arXiv DOI 别名、稀疏金标标题的顺序返回唯一证据类型。"""
    decision, evidence = compare_strong_identifiers(gold.model_dump(), predicted.model_dump())  # 复用通用严格身份裁决并保留别名证据类型。
    if decision:  # 强标识确认匹配时不需要稀疏标题规则。
        return evidence or "strict_identity"  # DOI arXiv 别名继续单独可审计展示。
    if _sparse_gold_title_match(gold, predicted):  # 最后仅处理 Gold 本身缺少消歧字段的标题完全一致。
        return "exact_title_sparse_gold"  # 明确标记为 PaSa 专用的审计回退。
    return None  # 不用模糊相似度或 LLM 推断补齐身份。


def audit_pasa_query(gold_query: GoldQuery, prediction: PredictionRecord, *, cutoff: int = 20) -> dict[str, object]:
    """对一条 PaSa 查询执行一对一身份审计，并保留最终排名位置。"""
    matched_gold: set[int] = set()  # 防止一篇 Gold 被多个预测重复计分。
    evidence: list[dict[str, object]] = []  # 保存逐篇可审阅的命中证据。
    for rank, predicted in enumerate(prediction.papers[:cutoff], start=1):  # 只审计赛题规定的最终 Top 20。
        for gold_index, gold_paper in enumerate(gold_query.relevant_papers):  # 按 Gold 原始稳定顺序寻找尚未使用的论文。
            if gold_index in matched_gold:  # 同一金标论文不能贡献第二次命中。
                continue  # 尝试下一个尚未命中的金标。
            kind = _match_kind(gold_paper, predicted)  # 取得严格或 PaSa 专用的证据类型。
            if kind is None:  # 当前预测与当前金标无法被确定性关联。
                continue  # 继续检查其余金标。
            matched_gold.add(gold_index)  # 锁定一对一命中关系。
            evidence.append({"gold_index": gold_index, "predicted_rank": rank, "match_kind": kind, "gold_arxiv_id": gold_paper.arxiv_id, "predicted_doi": predicted.doi, "predicted_arxiv_id": predicted.arxiv_id, "title": predicted.title})  # 保留最小身份审计事实。
            break  # 当前预测只允许匹配一篇金标。
    unique_predictions, _ = deduplicate_papers(prediction.papers[:cutoff])  # 与严格指标一致地以截断内唯一论文作为精确率分母。
    unique_prediction_count = len(unique_predictions)  # 保存可与既有 Precision@20 直接比较的分母。
    relevant_count = len(gold_query.relevant_papers)  # PaSa Gold 的相关论文数量已在导入阶段去重。
    true_positive = len(evidence)  # 每条证据对应一个唯一命中。
    precision = true_positive / unique_prediction_count if unique_prediction_count else 0.0  # 空最终列表不产生虚假精确率。
    recall = true_positive / relevant_count if relevant_count else 0.0  # 空金标按零处理而不产生满分。
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0  # 计算单查询调和平均。
    return {"query_id": gold_query.query_id, "cutoff": cutoff, "true_positive": true_positive, "predicted_count": unique_prediction_count, "relevant_count": relevant_count, "precision": precision, "recall": recall, "f1": f1, "evidence": evidence, "evidence_counts": dict(Counter(item["match_kind"] for item in evidence))}  # 返回报告和 JSONL 所需的完整审计行。


def summarize_pasa_identity_audit(gold_queries: list[GoldQuery], predictions: dict[str, PredictionRecord], *, cutoff: int = 20) -> dict[str, object]:
    """汇总固定集合的 PaSa 稀疏金标审计指标与可观测性边界。"""
    rows = [audit_pasa_query(gold, predictions[gold.query_id], cutoff=cutoff) for gold in gold_queries]  # 按固定 manifest 顺序审计所有查询。
    true_positive = sum(int(item["true_positive"]) for item in rows)  # 汇总唯一命中数。
    predicted_count = sum(int(item["predicted_count"]) for item in rows)  # 汇总最终列表真实分母。
    relevant_count = sum(int(item["relevant_count"]) for item in rows)  # 汇总 PaSa Gold 分母。
    micro_precision = true_positive / predicted_count if predicted_count else 0.0  # 计算跨查询微精确率。
    micro_recall = true_positive / relevant_count if relevant_count else 0.0  # 计算跨查询微召回率。
    micro_f1 = 2 * micro_precision * micro_recall / (micro_precision + micro_recall) if micro_precision + micro_recall else 0.0  # 计算微 F1。
    return {"matching_policy": "pasa-sparse-gold-audit-v2", "cutoff": cutoff, "query_count": len(rows), "macro_precision": sum(float(item["precision"]) for item in rows) / len(rows) if rows else 0.0, "macro_recall": sum(float(item["recall"]) for item in rows) / len(rows) if rows else 0.0, "macro_f1": sum(float(item["f1"]) for item in rows) / len(rows) if rows else 0.0, "micro_precision": micro_precision, "micro_recall": micro_recall, "micro_f1": micro_f1, "zero_hit_query_count": sum(int(item["true_positive"]) == 0 for item in rows), "at_least_one_hit_ratio": sum(int(item["true_positive"]) > 0 for item in rows) / len(rows) if rows else 0.0, "evidence_counts": dict(Counter(evidence["match_kind"] for item in rows for evidence in item["evidence"])), "query_rows": rows}  # 返回固定集合汇总和逐条审计行。
