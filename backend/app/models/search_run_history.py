"""定义本地搜索运行历史的最小读取与删除响应契约。"""

from datetime import datetime  # 返回 SQLite 记录的创建和最近更新时间。

from pydantic import BaseModel, Field  # 提供稳定的历史列表响应校验。

from backend.app.models.paper import PaperSource  # 返回本次实际参与的已知学术来源。
from backend.app.models.search_run import SearchRunStatus  # 返回运行可恢复或可删除的当前状态。


class SearchRunHistoryItem(BaseModel):
    """描述一条可恢复的本地搜索运行索引。

    属性：
        run_id：可用于恢复、查看详情或删除的稳定运行标识。
        query_text：用户本次提交的搜索问题，仅供其本地历史列表单行展示。
        status：运行当前状态，只有终态运行可由删除接口清理。
        current_round：最后保存的轮次进度。
        max_rounds：本次运行允许的最大轮次数。
        selected_sources：已实际选择的学术来源。
        stop_reason：终态时可展示的安全停止原因。
        result_ready：是否存在同次完整最终结果快照。
        created_at：首次保存状态的 UTC 时间。
        updated_at：最近写入状态或结果的 UTC 时间。
    """

    run_id: str  # 返回恢复和删除所需的稳定运行标识。
    query_text: str = Field(min_length=1, max_length=2000)  # 返回本地历史展示所需原始搜索问题，不返回论文内容。
    status: SearchRunStatus  # 返回已保存工作流状态。
    current_round: int = Field(ge=0)  # 返回最近已完成或正在执行的轮次。
    max_rounds: int = Field(ge=1)  # 返回本次运行最大轮次数。
    selected_sources: list[PaperSource] = Field(default_factory=list)  # 返回实际选择的来源而不暴露查询文本。
    stop_reason: str | None = None  # 返回安全可展示的终态停止原因。
    result_ready: bool  # 标记是否可以恢复完整结果页。
    created_at: datetime  # 返回状态记录首次创建时间。
    updated_at: datetime  # 返回状态或完整结果最近更新时间。


class SearchRunHistoryPage(BaseModel):
    """保存按最近更新时间倒序排列的有限本地运行历史列表。"""

    items: list[SearchRunHistoryItem] = Field(default_factory=list)  # 返回包含搜索问题但不包含论文内容的索引项。
    limit: int = Field(ge=1, le=50)  # 返回实际生效的列表读取上限。
