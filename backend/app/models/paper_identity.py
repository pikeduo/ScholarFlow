"""提供生产融合与离线评测共同使用的确定性论文身份规则。"""

from __future__ import annotations  # 延迟解析类型注解，保持纯函数在较旧诊断解释器中也可加载。

import re  # 解析已知标识前缀、尾部展示字符与 arXiv 版本号。
import unicodedata  # 统一不同来源返回的兼容 Unicode 表示。
from collections.abc import Mapping  # 接收不依赖具体领域模型的标识字段映射。


_ARXIV_DOI_PATTERN = re.compile(r"^10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})(?:v\d+)?$", re.IGNORECASE)  # 只接受 DataCite 固定 arXiv DOI 形式。


def _strip_known_prefix(value: str | None, prefixes: tuple[str, ...]) -> str | None:
    """统一字符、大小写并移除一个已知展示前缀。"""
    if value is None:  # 缺失字段必须保持为缺失。
        return None  # 不把缺失值伪装为可匹配空字符串。
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()  # 统一兼容字符并忽略标识大小写差异。
    for prefix in prefixes:  # 逐个检查显式允许的来源 URL 或文本前缀。
        if normalized.startswith(prefix):  # 只删除开头前缀，避免误伤标识主体。
            normalized = normalized[len(prefix):].strip()  # 保留去前缀后的真实主体。
            break  # 一个输入最多移除一个已知前缀。
    return normalized or None  # 空白或纯前缀输入不构成有效标识。


def normalize_doi(value: str | None) -> str | None:
    """将 DOI URL、dx.doi URL 或 ``doi:`` 文本统一为小写主体。"""
    normalized = _strip_known_prefix(value, ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"))  # 删除已知 DOI 展示前缀。
    return normalized.rstrip(" .;,:)]}") or None if normalized else None  # 清理引用文字尾部标点而不修改原始字段。


def normalize_arxiv_id(value: str | None) -> str | None:
    """统一 arXiv URL、前缀、PDF 后缀和版本号。"""
    normalized = _strip_known_prefix(value, ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "https://arxiv.org/pdf/", "http://arxiv.org/pdf/", "arxiv:"))  # 删除已知 arXiv 展示前缀。
    if normalized is None:  # 缺失或空白值不可形成预印本身份。
        return None  # 保持空值语义。
    normalized = normalized.split("?", maxsplit=1)[0]  # 先删除 URL 查询参数。
    normalized = normalized[:-4] if normalized.endswith(".pdf") else normalized  # 兼容较旧解释器地删除 PDF 展示后缀。
    return re.sub(r"v\d+$", "", normalized) or None  # 基础 arXiv ID 忽略同一论文的版本后缀。


def arxiv_id_from_doi(value: str | None) -> str | None:
    """仅从已知 arXiv DOI 别名确定性解析基础 arXiv 标识。"""
    normalized_doi = normalize_doi(value)  # 先统一 DOI URL、大小写和展示标点。
    match = _ARXIV_DOI_PATTERN.fullmatch(normalized_doi or "")  # 普通 DOI 必须被明确排除。
    return normalize_arxiv_id(match.group(1)) if match else None  # 复用 arXiv 版本规范化保证结果一致。


def normalize_pmid(value: str | None) -> str | None:
    """统一 PubMed URL 和 PMID 文本前缀，仅接受十进制 PMID。"""
    normalized = _strip_known_prefix(value, ("https://pubmed.ncbi.nlm.nih.gov/", "http://pubmed.ncbi.nlm.nih.gov/", "pmid:"))  # 删除允许的 PMID 展示前缀。
    candidate = normalized.strip("/ ") if normalized else None  # 清理 URL 尾部分隔符。
    return candidate if candidate and candidate.isdigit() else None  # 非数字文本不能作为 PMID 强标识。


def normalize_openalex_id(value: str | None) -> str | None:
    """统一 OpenAlex Work URL 与主体标识。"""
    normalized = _strip_known_prefix(value, ("https://openalex.org/", "http://openalex.org/"))  # 删除 OpenAlex URL 前缀。
    return normalized.strip("/") if normalized else None  # 保留小写的来源主体标识。


def normalize_semantic_scholar_id(value: str | None) -> str | None:
    """统一 Semantic Scholar 论文 URL 与主体标识。"""
    normalized = _strip_known_prefix(value, ("https://www.semanticscholar.org/paper/", "https://api.semanticscholar.org/graph/v1/paper/"))  # 删除已知 Semantic Scholar URL 前缀。
    return normalized.strip("/").rsplit("/", maxsplit=1)[-1] or None if normalized else None  # 兼容网页 URL 的标题 slug。


def normalize_dblp_key(value: str | None) -> str | None:
    """统一 DBLP 记录 URL、记录键和 HTML 后缀。"""
    normalized = _strip_known_prefix(value, ("https://dblp.org/rec/", "http://dblp.org/rec/"))  # 删除 DBLP 网页前缀。
    normalized = normalized[:-5] if normalized and normalized.endswith(".html") else normalized  # 兼容较旧解释器地删除 HTML 展示后缀。
    return normalized or None  # 保留可跨来源比较的记录键。


def normalize_internal_id(value: str | None) -> str | None:
    """统一同一离线契约内的内部论文标识。"""
    return _strip_known_prefix(value, ())  # 内部 ID 只统一 Unicode、空白与大小写。


def normalize_text(value: str | None) -> str | None:
    """将标题或作者规范化为仅含字母数字的保守比较文本。"""
    if value is None:  # 缺失文本不能成为标题或作者回退证据。
        return None  # 显式保持缺失语义。
    normalized = unicodedata.normalize("NFKC", value).casefold()  # 统一兼容字符和大小写。
    normalized = " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))  # 移除标点、下划线并压缩空白。
    return normalized or None  # 纯标点文本不能参与身份判断。


def identifier_values(fields: Mapping[str, str | None]) -> dict[str, set[str]]:
    """从论文标识字段得到比较键，并为 arXiv DOI 额外生成确定性别名。"""
    doi = normalize_doi(fields.get("doi"))  # 保留 DOI 自身的比较键。
    explicit_arxiv = normalize_arxiv_id(fields.get("arxiv_id"))  # 规范化供应商明确返回的 arXiv 标识。
    doi_arxiv_alias = arxiv_id_from_doi(fields.get("doi"))  # 仅为已知 DOI 格式派生 arXiv 比较别名。
    return {  # 返回各强标识 scheme 的全部可比较值。
        "doi": {doi} if doi else set(),  # DOI 不与其他 scheme 混用。
        "arxiv_id": {value for value in (explicit_arxiv, doi_arxiv_alias) if value},  # 显式 ID 与可验证 DOI 别名同时可用于 arXiv 比较。
        "pmid": {value for value in (normalize_pmid(fields.get("pmid")),) if value},  # PMID 使用数字主体比较。
        "openalex_id": {value for value in (normalize_openalex_id(fields.get("openalex_id")),) if value},  # OpenAlex ID 保留独立 scheme。
        "semantic_scholar_id": {value for value in (normalize_semantic_scholar_id(fields.get("semantic_scholar_id")),) if value},  # Semantic Scholar ID 保留独立 scheme。
        "dblp_key": {value for value in (normalize_dblp_key(fields.get("dblp_key")),) if value},  # DBLP key 保留独立 scheme。
    }


def compare_strong_identifiers(left: Mapping[str, str | None], right: Mapping[str, str | None]) -> tuple[bool | None, str | None]:
    """按 DOI、arXiv、PMID、平台 ID 的优先顺序比较强标识。

    返回 ``(True, evidence)`` 表示确定匹配，``(False, scheme)`` 表示同一强标识
    scheme 明确冲突，``(None, None)`` 表示没有可直接比较的同 scheme 标识。
    """
    left_values = identifier_values(left)  # 计算左侧所有规范化强标识与 DOI 别名。
    right_values = identifier_values(right)  # 计算右侧所有规范化强标识与 DOI 别名。
    for scheme in ("doi", "arxiv_id", "pmid", "openalex_id", "semantic_scholar_id", "dblp_key"):  # 严格按领域契约优先级裁决。
        current_left = left_values[scheme]  # 读取左侧当前 scheme 的全部确定性值。
        current_right = right_values[scheme]  # 读取右侧当前 scheme 的全部确定性值。
        if not current_left or not current_right:  # 任一侧缺失当前 scheme 时继续检查较低优先级。
            continue  # 不能将不同 scheme 视为已确认冲突。
        if current_left & current_right:  # 任一规范值交集即可确认同一论文。
            if scheme == "arxiv_id" and (arxiv_id_from_doi(left.get("doi")) or arxiv_id_from_doi(right.get("doi"))):  # 标注 DOI 派生的 arXiv 证据。
                return True, "arxiv_doi_alias"  # 供 PaSa 审计单独展示可验证别名。
            return True, scheme  # 返回稳定 scheme 证据。
        return False, scheme  # 同一优先级强标识不相同，禁止标题强行合并。
    return None, None  # 没有同 scheme 强标识可比较时允许调用方执行保守标题回退。
