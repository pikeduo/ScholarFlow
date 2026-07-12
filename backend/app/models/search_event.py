"""定义不包含查询正文和论文摘要的搜索进度 SSE 事件契约。"""

from datetime import datetime, timezone  # 生成跨时区一致的事件创建时间。
from typing import Literal  # 限制前端可稳定处理的事件类型。
from uuid import uuid4  # 为每条事件生成可用于断线补偿的稳定标识。

from pydantic import BaseModel, Field  # 提供 SSE 事件字段边界校验。


SearchEventType = Literal["run_created", "node_started", "node_completed", "progress", "source_degraded", "warning", "completed", "failed"]  # 限制首版工作流可发布的公共事件类型。


class SearchProgressEvent(BaseModel):
    """描述多轮搜索的单个可展示进度事件。

    属性：
        event_id：事件唯一标识，供前端去重和未来断线补偿使用。
        run_id：关联 SearchRunState 的稳定运行标识。
        event_type：当前事件所属的固定类别。
        node：产生事件的工作流或控制器节点名称。
        current_round：事件发生时已开始或已完成的轮次。
        progress：零到一之间的粗粒度进度，不承诺精确耗时预测。
        message：不包含完整查询、密钥或论文摘要的安全说明。
        metrics：仅保存数量、状态等轻量观测值。
        created_at：UTC 事件创建时间。
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))  # 生成前端可用于去重的事件标识。
    run_id: str = Field(min_length=1)  # 关联同一次多轮搜索运行。
    event_type: SearchEventType  # 限制事件类别以稳定前端分支处理。
    node: str | None = Field(default=None, max_length=100)  # 标记控制器或未来 LangGraph 节点名称。
    current_round: int | None = Field(default=None, ge=0, le=3)  # 保存当前轮次，初始创建事件允许为零。
    progress: float | None = Field(default=None, ge=0.0, le=1.0)  # 保存可展示但不过度承诺的归一化进度。
    message: str = Field(min_length=1, max_length=500)  # 保存经过净化的流程提示。
    metrics: dict[str, int | float | str | bool] = Field(default_factory=dict)  # 只保存安全的轻量统计而不携带论文详情。
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))  # 记录事件 UTC 创建时刻。
