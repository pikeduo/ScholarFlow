"""计算无需 LLM 裁判的确定性结构化输出代理分。"""

from statistics import fmean  # 计算论文级和查询级平均值。

from evaluation.contracts.prediction import PredictionRecord  # 读取预测列表、关系和分类。
from evaluation.contracts.result import StructureQueryScore, StructureSummary  # 返回稳定结构结果。
from evaluation.metrics.identifiers import deduplicate_papers, has_strong_identifier  # 复用身份与重复规则。


def _paper_completeness(paper) -> float:
    """按八组身份与展示字段计算单篇论文完整度。"""
    checks = (
        has_strong_identifier(paper),  # 要求至少一个可跨数据集复核的标识。
        bool(paper.title and paper.title.strip()),  # 要求非空标题。
        paper.year is not None,  # 要求发表年份。
        bool(paper.authors),  # 要求至少一名作者。
        bool(paper.venue and paper.venue.strip()),  # 要求期刊或会议信息。
        bool(paper.source and paper.source.strip()),  # 要求事实来源。
        paper.relevance_score is not None or paper.relevance_level is not None,  # 要求一种相关性表达。
        bool(paper.recommendation_reason and paper.recommendation_reason.strip()),  # 要求非空推荐理由。
    )
    return sum(checks) / len(checks)  # 返回零至一的确定性比例。


def _relation_legality(prediction: PredictionRecord) -> float:
    """校验关系和分类是否只引用当前结果集合的唯一 paper_id。"""
    paper_ids = {paper.paper_id for paper in prediction.papers if paper.paper_id}  # 收集集合内非空标识。
    checks: list[bool] = []  # 保存每条结构事实是否合法。
    checks.extend(relation.source in paper_ids and relation.target in paper_ids and relation.source != relation.target for relation in prediction.relations)  # 禁止集合外端点和自环。
    checks.extend(classification.paper_id in paper_ids for classification in prediction.classifications)  # 分类必须引用集合内论文。
    return sum(checks) / len(checks) if checks else 1.0  # 无可选结构事实时不扣分。


def score_structure_query(prediction: PredictionRecord) -> StructureQueryScore:
    """计算一条预测的列表合法性、字段完整度和关系合法性。"""
    if not prediction.papers:  # 空结果无法形成结构化论文输出。
        return StructureQueryScore(query_id=prediction.query_id, ranked_list_legality=0.0, field_completeness=0.0, relation_legality=0.0, proxy_score=0.0)  # 缺失预测不因可选关系为空得分。
    unique, duplicate_count = deduplicate_papers(prediction.papers)  # 审计有序列表中的重复论文。
    ranked_list_legality = 1.0 - (duplicate_count / len(prediction.papers))  # 重复项按原始列表长度线性扣分。
    field_completeness = fmean(_paper_completeness(paper) for paper in unique) if unique else 0.0  # 只对唯一论文评估字段质量。
    relation_legality = _relation_legality(prediction)  # 校验可选关系和分类引用。
    proxy_score = 0.4 * ranked_list_legality + 0.4 * field_completeness + 0.2 * relation_legality  # 使用固定透明权重合成本地代理分。
    return StructureQueryScore(query_id=prediction.query_id, ranked_list_legality=ranked_list_legality, field_completeness=field_completeness, relation_legality=relation_legality, proxy_score=proxy_score)  # 返回查询级分解。


def evaluate_structure(predictions: list[PredictionRecord]) -> StructureSummary:
    """聚合多条预测的确定性结构代理分。"""
    query_scores = [score_structure_query(prediction) for prediction in predictions]  # 保留每条查询的可审计分解。
    mean_score = fmean(score.proxy_score for score in query_scores) if query_scores else 0.0  # 空 fixture 返回零。
    return StructureSummary(mean_proxy_score=mean_score, query_scores=query_scores)  # 返回明确标记的非官方分数。
