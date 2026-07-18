"""提供不依赖外部服务的论文标识规范化、匹配与去重。"""

import re  # 提供标识前缀、版本号和标点清理。
import unicodedata  # 统一全角、兼容字符和大小写。
from collections.abc import Callable  # 标注字段规范化函数。

from evaluation.contracts.common import EvaluationPaper  # 使用统一论文输入契约。


def _strip_known_prefix(value: str | None, prefixes: tuple[str, ...]) -> str | None:
    """规范化大小写并移除一个已知 URL 或文本前缀。"""
    if value is None:  # 缺失值必须保持缺失。
        return None  # 不把缺失伪装为空标识。
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()  # 统一字符并忽略大小写。
    for prefix in prefixes:  # 按明确前缀逐一检查。
        if normalized.startswith(prefix):  # 只移除字符串开头的供应商格式。
            normalized = normalized[len(prefix):].strip()  # 保留真实标识主体。
            break  # 防止连续移除污染合法标识。
    return normalized or None  # 空字符串仍按缺失处理。


def normalize_doi(value: str | None) -> str | None:
    """将 DOI URL、dx.doi URL 或 ``doi:`` 文本统一为小写主体。"""
    normalized = _strip_known_prefix(value, ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"))  # 移除常见前缀。
    if normalized is None:  # 没有 DOI 时直接返回。
        return None  # 保留缺失语义。
    return normalized.rstrip(" .;,)") or None  # 清理引用文本末尾标点。


def normalize_arxiv_id(value: str | None) -> str | None:
    """统一 arXiv URL、前缀、PDF 后缀和版本号。"""
    normalized = _strip_known_prefix(value, ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "https://arxiv.org/pdf/", "http://arxiv.org/pdf/", "arxiv:"))  # 移除展示格式。
    if normalized is None:  # 没有 arXiv 标识时直接返回。
        return None  # 保留缺失语义。
    normalized = normalized.split("?", maxsplit=1)[0].removesuffix(".pdf")  # 移除查询参数和 PDF 后缀。
    return re.sub(r"v\d+$", "", normalized) or None  # 合并同一论文不同 arXiv 版本。


def normalize_pmid(value: str | None) -> str | None:
    """统一 PubMed URL 和 PMID 文本前缀。"""
    normalized = _strip_known_prefix(value, ("https://pubmed.ncbi.nlm.nih.gov/", "http://pubmed.ncbi.nlm.nih.gov/", "pmid:"))  # 移除常见展示前缀。
    if normalized is None:  # 没有 PMID 时直接返回。
        return None  # 保留缺失语义。
    candidate = normalized.strip("/ ")  # 清理 URL 尾部分隔符。
    return candidate if candidate.isdigit() else None  # PMID 只接受十进制数字。


def normalize_openalex_id(value: str | None) -> str | None:
    """统一 OpenAlex URL和主体标识。"""
    normalized = _strip_known_prefix(value, ("https://openalex.org/", "http://openalex.org/"))  # 移除 OpenAlex URL 前缀。
    return normalized.strip("/") if normalized else None  # 保留无尾部分隔符的小写工作标识。


def normalize_semantic_scholar_id(value: str | None) -> str | None:
    """统一 Semantic Scholar 论文 URL 和主体标识。"""
    normalized = _strip_known_prefix(value, ("https://www.semanticscholar.org/paper/", "https://api.semanticscholar.org/graph/v1/paper/"))  # 移除可识别 URL 前缀。
    if normalized is None:  # 没有平台标识时直接返回。
        return None  # 保留缺失语义。
    return normalized.strip("/").rsplit("/", maxsplit=1)[-1] or None  # 兼容带论文标题 slug 的网页 URL。


def normalize_dblp_key(value: str | None) -> str | None:
    """统一 DBLP 记录 URL、键和 HTML 后缀。"""
    normalized = _strip_known_prefix(value, ("https://dblp.org/rec/", "http://dblp.org/rec/"))  # 移除 DBLP 记录 URL。
    if normalized is None:  # 没有 DBLP 键时直接返回。
        return None  # 保留缺失语义。
    return normalized.removesuffix(".html") or None  # 统一网页与记录键形式。


def normalize_internal_id(value: str | None) -> str | None:
    """统一同一离线数据契约内的内部论文标识。"""
    return _strip_known_prefix(value, ())  # 内部标识只做字符、空白和大小写规范化。


def normalize_text(value: str | None) -> str | None:
    """将标题或作者规范化为仅含字母数字的比较文本。"""
    if value is None:  # 缺失文本不能参与回退匹配。
        return None  # 保留缺失语义。
    normalized = unicodedata.normalize("NFKC", value).casefold()  # 统一兼容字符与大小写。
    normalized = " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))  # 丢弃标点和下划线并稳定分词。
    return normalized or None  # 纯标点文本按缺失处理。


IDENTIFIER_FIELDS: tuple[tuple[str, Callable[[str | None], str | None]], ...] = (
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
    for field_name, normalizer in IDENTIFIER_FIELDS:  # 严格按领域身份优先级检查。
        left_value = normalizer(getattr(left, field_name))  # 规范化左侧标识。
        right_value = normalizer(getattr(right, field_name))  # 规范化右侧标识。
        if left_value and right_value:  # 两侧都有当前优先级标识时可直接裁决。
            return left_value == right_value  # 冲突强标识不再用标题强行合并。
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
