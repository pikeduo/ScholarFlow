"""定义从已保存搜索结果快照生成的事实型综合报告契约。"""

from pydantic import BaseModel, Field  # 提供报告字段与受控集合长度校验。

from backend.app.models.coverage import CoverageGap  # 回显既有覆盖分析生成的可验证缺口。
from backend.app.models.paper import PaperSource  # 约束来源统计仅使用已知学术来源。


class SearchSynthesisSource(BaseModel):
    """表示单个来源在当前已保存搜索运行中的召回与最终保留数量。"""

    source: PaperSource  # 保存稳定来源名称供前端展示。
    recalled_count: int = Field(ge=0)  # 保存各轮累计的来源成功返回数量。
    final_paper_count: int = Field(ge=0)  # 保存最终快照中以该来源为主记录的论文数量。


class SearchSynthesisKeyword(BaseModel):
    """表示来源论文关键词在本次最终结果中的出现频次。"""

    keyword: str = Field(min_length=1, max_length=200)  # 保留来源提供的可展示关键词。
    paper_count: int = Field(ge=1)  # 保存出现该关键词的不同论文数量。


class SearchSynthesisReport(BaseModel):
    """汇总同次完成结果的可审计结论，不包含模型自由生成内容。"""

    run_id: str = Field(min_length=1)  # 绑定报告到唯一已完成搜索运行。
    final_paper_count: int = Field(ge=0)  # 保存最终结果快照中的论文总数。
    high_relevance_count: int = Field(ge=0)  # 保存覆盖分析确认的高相关论文数量。
    partial_relevance_count: int = Field(ge=0)  # 保存待进一步确认的部分相关论文数量。
    not_satisfied_count: int = Field(ge=0)  # 保存未满足约束但仍在快照中的候选数量。
    year_start: int | None = Field(default=None, ge=1800, le=2100)  # 保存最终结果可确认的最早发表年份。
    year_end: int | None = Field(default=None, ge=1800, le=2100)  # 保存最终结果可确认的最晚发表年份。
    sources: list[SearchSynthesisSource] = Field(default_factory=list)  # 保存按稳定来源顺序的贡献统计。
    top_keywords: list[SearchSynthesisKeyword] = Field(default_factory=list, max_length=8)  # 保存最多八个来源关键词频次。
    coverage_gaps: list[CoverageGap] = Field(default_factory=list)  # 直接回显已保存的覆盖缺口，不重新推断。
    stop_reason: str | None = None  # 回显工作流已经确定的安全停止原因。
    findings: list[str] = Field(default_factory=list, max_length=5)  # 保存由事实字段模板化生成的摘要结论。
    follow_up_suggestions: list[str] = Field(default_factory=list, max_length=5)  # 保存只针对已知缺口的下一步建议。
