"""定义跨学术数据源统一使用的论文领域模型。"""

from datetime import datetime  # 记录来源拉取和规范化元数据的更新时间。
from typing import Literal  # 限制论文数据的已知来源范围。

from pydantic import BaseModel, Field  # 提供论文数据校验与字段约束。


PaperSource = Literal["openalex", "semantic_scholar", "arxiv", "dblp", "pubmed", "manual"]  # 标记可追溯的论文来源。
PaperType = Literal["article", "conference", "preprint", "review"]  # 标记统一论文可识别的基础类型。


class PaperAuthor(BaseModel):
    """描述论文作者及其可选的身份和机构信息。

    属性：
        name：作者显示名称。
        orcid：作者 ORCID 标识，数据源未提供时为空。
        institution：作者所属机构，数据源未提供时为空。
    """

    name: str = Field(min_length=1)  # 要求作者名称不可为空。
    orcid: str | None = None  # 保留可用于跨源作者匹配的 ORCID。
    institution: str | None = None  # 保留数据源提供的作者机构名称。
    source_author_ids: dict[str, str] = Field(default_factory=dict)  # 保留各来源提供的平台作者标识。


class PaperSourceRecord(BaseModel):
    """保存单个外部来源对论文的原始命中与溯源信息。

    属性：
        source：提供本条元数据的外部来源。
        external_id：来源内稳定论文标识。
        raw_rank：论文在该来源原始结果中的名次。
        matched_subqueries：命中该论文的子查询文本列表。
        fetched_at：从来源成功获取元数据的时间。
    """

    source: PaperSource  # 标记当前元数据记录的来源。
    external_id: str = Field(min_length=1)  # 确保每条溯源记录具有来源内稳定标识。
    raw_rank: int | None = Field(default=None, ge=1)  # 保留可选的来源原始排名。
    matched_subqueries: list[str] = Field(default_factory=list)  # 保存用于 RRF 和可解释性的命中子查询。
    fetched_at: datetime | None = None  # 保留来源拉取时间，缺失时不虚构时间戳。


class Paper(BaseModel):
    """保存来自不同学术数据源的规范化论文元数据。

    属性：
        paper_id：数据源内稳定的论文标识。
        title：论文标题。
        abstract：论文摘要，数据源缺失时为空字符串。
        authors：规范化作者列表。
        year：发表年份，数据源缺失时为空。
        venue：发表期刊或会议，数据源缺失时为空。
        doi：数字对象标识符，数据源缺失时为空。
        arxiv_id：arXiv 标识符，数据源缺失时为空。
        pmid：PubMed 标识符，数据源缺失时为空。
        citation_count：数据源报告的被引次数。
        references：该论文引用的上游论文标识列表。
        source：提供当前元数据的数据源。
    """

    paper_id: str = Field(min_length=1)  # 确保每条论文记录具有来源内唯一标识。
    title: str = Field(min_length=1)  # 确保界面和排序模块始终可展示标题。
    abstract: str = ""  # 允许部分数据源未返回摘要。
    authors: list[PaperAuthor] = Field(default_factory=list)  # 避免不同论文实例共享作者列表。
    year: int | None = Field(default=None, ge=1800, le=2100)  # 限制年份为合理出版范围。
    venue: str | None = None  # 保留可选的期刊或会议名称。
    doi: str | None = None  # 保留后续 DOI 优先去重所需原始标识。
    arxiv_id: str | None = None  # 保留后续预印本识别所需标识。
    pmid: str | None = None  # 保留医学文献跨来源去重所需 PubMed 标识。
    citation_count: int = Field(default=0, ge=0)  # 禁止出现无意义的负引用数。
    references: list[str] = Field(default_factory=list)  # 保存可用于引文图谱的引用标识。
    source: PaperSource  # 强制记录元数据来源便于溯源和纠错。


class PaperRecord(Paper):
    """扩展 Paper 的多源规范化记录，供融合、持久化和排序阶段使用。

    属性：
        keywords：来源返回或后续提取的关键词。
        paper_type：论文基础类型。
        openalex_id：OpenAlex 来源标识。
        semantic_scholar_id：Semantic Scholar 来源标识。
        dblp_key：DBLP 来源标识。
        is_open_access：来源声明的开放获取状态。
        open_access_url：可公开访问的合法链接。
        source_records：所有来源命中与原始排名记录。
        work_family_id：预印本、会议版与期刊版的版本族标识。
        rrf_score：基于各来源原始排名计算的融合分数。
        semantic_score：BGE-M3 密集向量计算的查询相关性分数。
        text_hash：用于判断摘要或标题变化的文本哈希。
        embedding_model_version：生成向量时使用的模型版本。
        updated_at：规范化记录最近更新时间。
    """

    keywords: list[str] = Field(default_factory=list)  # 保存可用于展示和向量编码的关键词。
    paper_type: PaperType | None = None  # 保留可选的来源论文类型。
    openalex_id: str | None = None  # 保留 OpenAlex 的稳定来源标识。
    semantic_scholar_id: str | None = None  # 保留 Semantic Scholar 的稳定来源标识。
    dblp_key: str | None = None  # 保留 DBLP 的稳定来源标识。
    is_open_access: bool | None = None  # 保留来源无法确认时的三态开放获取信息。
    open_access_url: str | None = None  # 保存经来源提供的合法开放访问链接。
    source_records: list[PaperSourceRecord] = Field(default_factory=list)  # 保存多源命中和原始排名的溯源记录。
    work_family_id: str | None = None  # 关联预印本、会议版与期刊版的版本族。
    rrf_score: float = Field(default=0.0, ge=0.0)  # 保存融合阶段计算的非负 Reciprocal Rank Fusion 分数。
    semantic_score: float | None = None  # 保存语义粗排阶段的可选相关性分数，模型降级时保持空值。
    text_hash: str | None = None  # 保存向量更新判断使用的标题摘要哈希。
    embedding_model_version: str | None = None  # 保存当前向量对应的模型版本。
    updated_at: datetime | None = None  # 保存规范化记录的最近更新时间。
