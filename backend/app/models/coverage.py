"""定义覆盖缺口分析与多轮搜索控制共享的数据契约。"""

from typing import Literal  # 限制前端可稳定展示的缺口类型集合。

from pydantic import BaseModel, Field  # 提供覆盖报告字段的边界校验。


CoverageGapType = Literal["must_include", "dataset", "method", "year_range", "source", "result_count"]  # 标记需要补足的检索维度。


class CoverageGap(BaseModel):
    """描述当前最终候选集中一个可解释、可用于演化查询的覆盖缺口。

    属性：
        gap_type：缺口所属的检索维度。
        constraint：缺失或不足的具体约束文本。
        severity：零到一之间的缺口严重程度。
        current_match_count：当前最终候选中显式匹配该约束的数量。
        recommended_query_focus：供后续 Query Evolution 使用的聚焦文本。
    """

    gap_type: CoverageGapType  # 保存供前端分组和后续演化策略使用的稳定类型。
    constraint: str = Field(min_length=1)  # 防止产生没有可解释对象的空缺口。
    severity: float = Field(ge=0.0, le=1.0)  # 限制严重程度为可比较的归一化区间。
    current_match_count: int = Field(default=0, ge=0)  # 保存当前已显式覆盖该约束的论文数量。
    recommended_query_focus: str = Field(min_length=1)  # 保存不改写硬约束的下一轮查询聚焦建议。


class CoverageReport(BaseModel):
    """汇总当前一轮搜索的约束覆盖、边际收益与是否值得继续的判断。

    属性：
        target_count：本轮检索希望得到的高相关论文数量。
        high_relevance_count：同时满足核验与相关性门槛的论文数量。
        partial_relevance_count：相关但约束证据仍不充分的论文数量。
        gaps：按严重度排序的覆盖缺口列表。
        new_valid_count：本轮相对上一轮新增的高质量论文数量。
        marginal_gain：新增高质量论文相对于目标数量的归一化收益。
        should_continue：在预算、来源和轮次均允许时，控制器是否应尝试下一轮。
        stop_reason：不应继续时可安全展示的停止原因。
    """

    target_count: int = Field(ge=1)  # 保存 QueryIntent 定义的最终高相关论文目标。
    high_relevance_count: int = Field(default=0, ge=0)  # 保存当前高相关且已核验的候选数量。
    partial_relevance_count: int = Field(default=0, ge=0)  # 保存可展示但需进一步核验的候选数量。
    gaps: list[CoverageGap] = Field(default_factory=list)  # 保存按优先级排序的待补足约束。
    new_valid_count: int = Field(default=0, ge=0)  # 保存本轮新增的高质量论文数量。
    marginal_gain: float = Field(default=0.0, ge=0.0, le=1.0)  # 保存相对目标数量的边际收益。
    should_continue: bool = False  # 指示后续多轮控制器是否值得继续，而非自行触发调用。
    stop_reason: str | None = None  # 保存正常完成、预算或收益不足等安全停止摘要。
