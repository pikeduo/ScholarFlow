"""定义检索服务返回的论文列表、阶段统计和运行摘要。"""

from pydantic import BaseModel, Field, SerializeAsAny  # 提供稳定的服务输出数据模型和子类序列化支持。

from backend.app.models.paper import Paper  # 复用基础论文模型及其多源规范化子类。


class SearchResult(BaseModel):
    """描述一次检索运行可向 API 层返回的论文和阶段统计。

    属性：
        papers：保持数据源召回顺序的去重和规则过滤后论文。
        recalled_count：客户端成功映射的原始论文数量。
        deduplicated_count：去重后可交给后续排序阶段的论文数量。
        filtered_count：本地规则过滤移除的论文数量。
        run_id：关联 SearchRunState 的可选运行标识。
        source_counts：按来源统计的成功召回数量。
        stop_reason：运行结束或提前停止的可解释原因。
        warnings：可安全展示给调用方的来源降级或约束警告。
    """

    papers: list[SerializeAsAny[Paper]] = Field(default_factory=list)  # 兼容单源 Paper 并保留多源 PaperRecord 的额外溯源字段。
    recalled_count: int = Field(ge=0)  # 记录数据源本轮成功召回的论文数。
    deduplicated_count: int = Field(ge=0)  # 记录去重后保留的论文数。
    filtered_count: int = Field(default=0, ge=0)  # 记录本地规则过滤移除的论文数。
    run_id: str | None = None  # 关联可恢复搜索运行的可选标识。
    source_counts: dict[str, int] = Field(default_factory=dict)  # 保存多来源成功召回数量。
    stop_reason: str | None = None  # 保存正常完成、预算触顶或无新增结果等停止原因。
    warnings: list[str] = Field(default_factory=list)  # 保存不含敏感信息的可展示警告。
