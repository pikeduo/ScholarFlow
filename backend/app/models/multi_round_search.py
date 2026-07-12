"""定义多轮检索控制器的最终运行结果契约。"""

from pydantic import BaseModel, Field  # 提供多轮结果的稳定字段校验。

from backend.app.models.coverage import CoverageReport  # 保存累计候选的最终覆盖判断。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 保持网页补充发现与论文结果分离。
from backend.app.models.paper import PaperRecord  # 保存跨轮去重后的最终论文候选。
from backend.app.models.query_intent import QueryIntent  # 回显实际执行或自然语言规划得到的搜索意图。
from backend.app.models.search_run import SearchRunState  # 回传可恢复、可审计的运行状态。


class MultiRoundSearchResult(BaseModel):
    """保存多轮搜索结束后的累计论文、来源统计、覆盖报告与运行状态。

    属性：
        run_state：可供持久化、SSE 与恢复流程使用的最终运行状态。
        query_intent：本次多轮搜索实际执行的完整结构化意图。
        papers：跨轮身份去重后的最终论文候选。
        discoveries：各轮独立汇总的补充网页发现项。
        source_counts：所有轮次按来源累计的成功结果数量。
        source_errors：按来源保存的最新安全错误摘要。
        coverage_report：针对累计候选重新计算的最终覆盖报告。
        query_planning_model_name：自然语言入口实际使用的 Query Agent 模型名称。
        query_planning_prompt_tokens：自然语言规划输入 Token 数量。
        query_planning_completion_tokens：自然语言规划输出 Token 数量。
        query_planning_duration_ms：自然语言规划耗时。
    """

    run_state: SearchRunState  # 保存轮次、预算统计、停止原因和最终候选引用。
    query_intent: QueryIntent  # 回显实际执行计划，供前端解释和用户编辑重搜。
    papers: list[PaperRecord] = Field(default_factory=list)  # 保存不使用低相关论文凑数的跨轮结果。
    discoveries: list[SupplementalDiscoveryItem] = Field(default_factory=list)  # 保存不可合并为论文的网页补充发现。
    source_counts: dict[str, int] = Field(default_factory=dict)  # 保存跨轮累计来源结果数。
    source_errors: dict[str, str] = Field(default_factory=dict)  # 保存不含内部细节的最新来源错误摘要。
    coverage_report: CoverageReport | None = None  # 保存最终控制决策所依据的覆盖报告。
    query_planning_model_name: str | None = None  # 直接意图重搜时为空，自然语言入口回显实际规划模型。
    query_planning_prompt_tokens: int = Field(default=0, ge=0)  # 保存 Query Agent 输入 Token 用量。
    query_planning_completion_tokens: int = Field(default=0, ge=0)  # 保存 Query Agent 输出 Token 用量。
    query_planning_duration_ms: int = Field(default=0, ge=0)  # 保存 Query Agent 调用与解析耗时。
