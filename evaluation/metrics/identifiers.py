"""提供不依赖外部服务的论文标识规范化、匹配与去重。"""

from evaluation.contracts.common import EvaluationPaper  # 使用统一论文输入契约。
from backend.app.models.paper_identity import compare_strong_identifiers, normalize_arxiv_id, normalize_dblp_key, normalize_doi, normalize_internal_id, normalize_openalex_id, normalize_pmid, normalize_semantic_scholar_id, normalize_text  # 复用生产融合所用的纯身份规则。


IDENTIFIER_FIELDS = (
    ("doi", normalize_doi),  # DOI 是最高优先级跨来源身份。
    ("arxiv_id", normalize_arxiv_id),  # arXiv 标识在去版本后匹配。
    ("pmid", normalize_pmid),  # PMID 用于医学论文身份。
    ("openalex_id", normalize_openalex_id),  # OpenAlex 平台 ID 次于标准标识。
    ("semantic_scholar_id", normalize_semantic_scholar_id),  # Semantic Scholar ID 作为平台身份。
    ("dblp_key", normalize_dblp_key),  # DBLP 键作为计算机领域平台身份。
)


def has_strong_identifier(paper: EvaluationPaper) -> bool:
    """判断论文是否具有至少一个可优先匹配的稳定标识。"""
    for field_name, normalizer in IDENTIFIER_FIELDS:  # 只检查可跨数据集比较的稳定标识。
        if normalizer(getattr(paper, field_name)):  # 任一稳定标识有效即可。
            return True  # 返回存在强标识。
    return False  # 标题回退记录需要计入缺失标识统计。


def papers_match(left: EvaluationPaper, right: EvaluationPaper) -> bool:
    """按稳定标识优先、标题年份作者回退的保守规则判断同一论文。"""
    decision, _ = compare_strong_identifiers(left.model_dump(), right.model_dump())  # 复用生产强标识优先级与 DOI arXiv 别名规则。
    if decision is not None:  # 有可比较强标识时必须直接裁决。
        return decision  # 强标识冲突不得退回标题。
    left_internal_id = normalize_internal_id(left.paper_id)  # 规范化左侧 fixture 内部标识。
    right_internal_id = normalize_internal_id(right.paper_id)  # 规范化右侧 fixture 内部标识。
    if left_internal_id and right_internal_id and left_internal_id == right_internal_id:  # 相同内部标识可直接匹配。
        return True  # 不把不同 fixture 的内部标识冲突视为论文身份冲突。
    left_title = normalize_text(left.title)  # 规范化左侧标题。
    right_title = normalize_text(right.title)  # 规范化右侧标题。
    if not left_title or left_title != right_title:  # 标题缺失或不一致时不能回退合并。
        return False  # 保守避免误匹配。
    if left.year is not None and right.year is not None and left.year != right.year:  # 明确年份冲突时拒绝合并。
        return False  # 保护同名论文。
    left_author = normalize_text(left.authors[0]) if left.authors else None  # 提取左侧第一作者。
    right_author = normalize_text(right.authors[0]) if right.authors else None  # 提取右侧第一作者。
    if left_author and right_author:  # 两侧均有作者时要求第一作者一致。
        return left_author == right_author  # 使用作者降低同名论文碰撞。
    return left.year is not None and right.year is not None  # 缺作者时仅允许标题与明确相同年份组合匹配。


def deduplicate_papers(papers: list[EvaluationPaper]) -> tuple[list[EvaluationPaper], int]:
    """按原始顺序去重并返回唯一论文与重复数量。"""
    unique: list[EvaluationPaper] = []  # 保留首个出现位置以维持排名。
    duplicate_count = 0  # 记录被去除的后续重复项。
    for paper in papers:  # 逐条进行保守匹配。
        if any(papers_match(paper, existing) for existing in unique):  # 只与此前保留项比较。
            duplicate_count += 1  # 重复预测不得被后续论文补位而隐藏。
            continue  # 丢弃当前重复项。
        unique.append(paper)  # 首次出现的论文进入唯一列表。
    return unique, duplicate_count  # 返回稳定顺序和审计计数。
