"""定义跨学术数据源统一使用的论文领域模型。"""

from typing import Literal  # 限制论文数据的已知来源范围。

from pydantic import BaseModel, Field  # 提供论文数据校验与字段约束。


PaperSource = Literal["openalex", "semantic_scholar", "arxiv", "dblp", "pubmed", "manual"]  # 标记可追溯的论文来源。


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
