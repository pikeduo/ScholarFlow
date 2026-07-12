"""构造论文与查询的版本化语义文本，并生成向量缓存使用的稳定哈希。"""

from dataclasses import dataclass  # 使用不可变值对象在服务间安全传递构造结果。
from hashlib import sha256  # 使用跨进程稳定的 SHA-256 判断向量是否需要更新。
from re import sub  # 统一字段中的连续空白，避免展示差异造成重复编码。

from backend.app.models.paper import PaperRecord  # 读取已规范化的公开论文元数据。
from backend.app.models.query_intent import QueryIntent  # 读取结构化查询意图构造查询语义文本。


PAPER_EMBEDDING_TEXT_VERSION = "paper_embedding_text_v1"  # 标记当前论文向量文本格式，格式变化时必须递增版本。
PAPER_RERANKER_TEXT_VERSION = "paper_reranker_text_v1"  # 标记当前 Cross Encoder 论文侧文本格式。
QUERY_TEXT_VERSION = "query_text_v1"  # 标记当前结构化查询文本格式。


class PaperTextBuilderError(ValueError):
    """表示论文文本构造所需的公开元数据不满足最小边界。"""


@dataclass(frozen=True)
class BuiltText:
    """保存可供模型使用的文本、格式版本与缓存判断哈希。"""

    text: str  # 保存实际传给嵌入或重排模型的文本。
    builder_version: str  # 保存可触发向量失效的构造规则版本。
    text_hash: str  # 保存供 SQLite 向量元数据比较的稳定十六进制摘要。


class PaperTextBuilder:
    """集中构造 BGE、Cross Encoder 与查询文本，隔离字段选择和哈希规则。

    参数：
        embedding_model_name：生成论文向量的模型名称，用于缓存失效判断。
        embedding_model_revision：可选模型修订版本，升级权重时应随之变化。
        embedding_abstract_char_limit：嵌入文本中摘要的最大字符数。
    异常：
        PaperTextBuilderError：论文标题缺失或仅包含空白时抛出。
    """

    def __init__(self, embedding_model_name: str = "BAAI/bge-m3", embedding_model_revision: str | None = None, embedding_abstract_char_limit: int = 12000) -> None:
        """保存文本策略配置，不加载模型也不访问文件系统。"""
        if not embedding_model_name.strip():  # 空模型名称会让不同模型错误复用缓存。
            raise ValueError("embedding_model_name 不能为空")  # 尽早暴露无法生成可靠哈希的配置错误。
        if embedding_abstract_char_limit < 1:  # 零长度限制会丢失所有摘要语义。
            raise ValueError("embedding_abstract_char_limit 必须大于零")  # 保证截断策略可用。
        self._embedding_model_name = embedding_model_name.strip()  # 保存规范化模型名称用于文本哈希。
        self._embedding_model_revision = _normalize_optional_text(embedding_model_revision)  # 保存可选权重修订版本。
        self._embedding_abstract_char_limit = embedding_abstract_char_limit  # 保存嵌入模型可承受的摘要字符上限。

    def build_embedding_text(self, paper: PaperRecord) -> BuiltText:
        """构造论文向量文本，始终保留标题并仅使用公开结构化元数据。"""
        title = _require_title(paper.title)  # 标题是缺摘要论文唯一且必须保留的语义线索。
        keywords = _join_values(paper.keywords)  # 规范化关键词，避免空值或连续空白影响哈希。
        abstract = _normalize_text(paper.abstract)[: self._embedding_abstract_char_limit]  # 仅截断摘要，不截断标题或关键词。
        venue = _normalize_optional_text(paper.venue) or "Unknown"  # 缺失 venue 时使用稳定占位符保持字段结构。
        year = str(paper.year) if paper.year is not None else "Unknown"  # 缺失年份时不虚构元数据。
        text = "\n".join((f"Title: {title}", f"Keywords: {keywords}", f"Abstract: {abstract}", f"Venue: {venue}", f"Year: {year}"))  # 使用固定字段顺序保证重建索引可复现。
        return self._build_result(text, PAPER_EMBEDDING_TEXT_VERSION)  # 返回可用于向量缓存检查的版本化结果。

    def build_reranker_text(self, paper: PaperRecord) -> BuiltText:
        """构造 Cross Encoder 论文侧文本，避免引入来源原始响应和非语义统计值。"""
        title = _require_title(paper.title)  # Cross Encoder 同样必须能在缺摘要场景下使用标题。
        abstract = _normalize_text(paper.abstract)  # 精排保留完整摘要，由模型自身最大长度策略处理截断。
        text = f"{title}\n{abstract}" if abstract else title  # 摘要缺失时不追加无意义空行。
        return self._build_result(text, PAPER_RERANKER_TEXT_VERSION)  # 返回与嵌入文本格式独立的版本化结果。

    def build_query_text(self, query: QueryIntent) -> BuiltText:
        """按 QueryIntent 的硬约束优先级构造跨语言语义查询文本。"""
        sections = [("Research topic", _normalize_text(query.normalized_query))]  # 始终以规范化查询作为主题主线。
        sections.extend((("Required methods", _join_values(query.methods)), ("Required datasets", _join_values(query.datasets)), ("Tasks", _join_values(query.tasks)), ("Must include", _join_values(query.must_include)), ("Should include", _join_values(query.should_include)), ("Exclude", _join_values(query.exclude))))  # 将硬条件置于软偏好和排除说明之前。
        text = "\n".join(f"{label}: {value}" for label, value in sections if value)  # 跳过空条件，避免将占位文本误送入模型。
        return self._build_result(text, QUERY_TEXT_VERSION)  # 查询哈希可供后续向量缓存或运行审计复用。

    def _build_result(self, text: str, builder_version: str) -> BuiltText:
        """使用模型身份、构造版本和文本生成可跨重启复现的哈希。"""
        hash_input = "\x00".join((self._embedding_model_name, self._embedding_model_revision or "", builder_version, text))  # 使用不可见分隔符消除字段拼接歧义。
        text_hash = sha256(hash_input.encode("utf-8")).hexdigest()  # 显式 UTF-8 编码保证 Windows 与其他平台结果一致。
        return BuiltText(text=text, builder_version=builder_version, text_hash=text_hash)  # 统一封装所有构造结果。


def _require_title(value: str) -> str:
    """返回规范化标题；仅空白标题时拒绝构造不可解释的论文文本。"""
    title = _normalize_text(value)  # 先统一来源标题中的换行和连续空白。
    if not title:  # Pydantic 最小长度无法阻止仅含空格的历史数据。
        raise PaperTextBuilderError("论文标题不能为空")  # 向调用方提供不包含来源细节的稳定错误。
    return title  # 返回始终可作为模型输入第一行的标题。


def _join_values(values: list[str]) -> str:
    """清理列表字段、移除空项并以稳定分隔符连接。"""
    return ", ".join(normalized for value in values if (normalized := _normalize_text(value)))  # 保留原始顺序，避免擅自改变关键词或约束优先级。


def _normalize_optional_text(value: str | None) -> str | None:
    """规范化可选文本，并将空白值转换为缺失值。"""
    normalized = _normalize_text(value or "")  # 允许来源缺失字段并统一处理空白。
    return normalized or None  # 避免空字符串进入模型文本或哈希字段。


def _normalize_text(value: str) -> str:
    """将 Unicode 文本中的连续空白压缩为单个空格，保持语义内容不变。"""
    return sub(r"\s+", " ", value).strip()  # 统一换行、制表符和多余空格，保证哈希只反映语义文本变化。
