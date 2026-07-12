"""定义多轮检索控制器的最终运行结果契约。"""

from pydantic import BaseModel, Field  # 提供多轮结果的稳定字段校验。

from backend.app.models.coverage import CoverageReport  # 保存累计候选的最终覆盖判断。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 保持网页补充发现与论文结果分离。
from backend.app.models.paper import PaperRecord  # 保存跨轮去重后的最终论文候选。
from backend.app.models.search_run import SearchRunState  # 回传可恢复、可审计的运行状态。


class MultiRoundSearchResult(BaseModel):
    """保存多轮搜索结束后的累计论文、来源统计、覆盖报告与运行状态。

    属性：
        run_state：可供持久化、SSE 与恢复流程使用的最终运行状态。
        papers：跨轮身份去重后的最终论文候选。
        discoveries：各轮独立汇总的补充网页发现项。
        source_counts：所有轮次按来源累计的成功结果数量。
        source_errors：按来源保存的最新安全错误摘要。
        coverage_report：针对累计候选重新计算的最终覆盖报告。
    """

    run_state: SearchRunState  # 保存轮次、预算统计、停止原因和最终候选引用。
    papers: list[PaperRecord] = Field(default_factory=list)  # 保存不使用低相关论文凑数的跨轮结果。
    discoveries: list[SupplementalDiscoveryItem] = Field(default_factory=list)  # 保存不可合并为论文的网页补充发现。
    source_counts: dict[str, int] = Field(default_factory=dict)  # 保存跨轮累计来源结果数。
    source_errors: dict[str, str] = Field(default_factory=dict)  # 保存不含内部细节的最新来源错误摘要。
    coverage_report: CoverageReport | None = None  # 保存最终控制决策所依据的覆盖报告。
