"""定义已保存搜索结果筛选、排序与分页的稳定读取契约。"""

from typing import Literal  # 限制前后端共同支持的确定性排序选项。

from pydantic import BaseModel, Field  # 提供只读分页响应字段校验。

from backend.app.models.paper import PaperRecord  # 返回与详情和比较一致的规范化论文事实。


SearchResultSort = Literal["relevance", "year_desc", "citation_desc"]  # 限制首版可解释排序策略，避免开放任意字段排序。
SearchResultRelevance = Literal["satisfied", "uncertain", "not_satisfied"]  # 限制与论文约束核验状态一致的筛选值。


class SearchRunPaperPage(BaseModel):
    """保存单次搜索运行中经过筛选与排序的一页已保存论文。

    属性：
        run_id：与 SSE、恢复和结果快照关联的稳定运行标识。
        items：当前页的规范化论文事实，不含重新计算的字段。
        total：筛选与排序前分页后的总匹配数量。
        page：服务端校正后的当前页码。
        page_size：当前页最大论文数量。
        total_pages：基于总匹配数计算的最少页数，空集合也固定为一页。
    """

    run_id: str  # 返回所属已保存搜索运行标识。
    items: list[PaperRecord] = Field(default_factory=list)  # 返回当前页论文，不伪造空论文。
    total: int = Field(ge=0)  # 返回筛选命中的总数量。
    page: int = Field(ge=1)  # 返回经服务端校正后的当前页。
    page_size: int = Field(ge=1, le=20)  # 返回实际生效的单页数量。
    total_pages: int = Field(ge=1)  # 空集合保留一页，简化前端分页控件边界。
