"""定义 LangGraph 与业务服务依赖的统一学术来源适配器协议。"""

from typing import Protocol, runtime_checkable  # 声明可替换且可在测试中检查的异步适配器契约。

from backend.app.models.discovery import DiscoverySource, SupplementalDiscoveryItem  # 使用独立的网页补充发现来源与结果模型。
from backend.app.models.paper import PaperRecord, PaperSource  # 使用统一多源论文模型和来源枚举。
from backend.app.models.query_intent import QueryIntent  # 使用查询规划节点输出的统一意图。


@runtime_checkable
class AcademicSearchAdapter(Protocol):
    """约束学术数据源在首版必须提供的论文搜索能力。

    属性：
        source：适配器对应的稳定来源名称。
    方法：
        search：以 QueryIntent 执行一次来源检索并返回可溯源的 PaperRecord 列表。
    """

    source: PaperSource  # 标记适配器实现对应的学术数据来源。

    async def search(self, query: QueryIntent) -> list[PaperRecord]:
        """执行单来源搜索并转换为统一论文记录。

        参数：
            query：已由 Query Agent 校验的结构化检索意图。
        返回：
            list[PaperRecord]：保留来源溯源和原始排名的规范化论文列表。
        异常：
            RuntimeError：来源不可用、限流或响应结构异常时抛出已净化错误。
        """
        ...  # Protocol 仅声明业务边界，不实现网络、鉴权或字段映射。


@runtime_checkable
class WebDiscoveryAdapter(Protocol):
    """约束不能直接合并为论文的补充网页发现来源。

    属性：
        source：适配器对应的稳定补充发现来源名称。
    方法：
        discover：以 QueryIntent 执行补充网页发现并返回不可合并条目。
    """

    source: DiscoverySource  # 标记适配器实现对应的补充发现来源。

    async def discover(self, query: QueryIntent) -> list[SupplementalDiscoveryItem]:
        """执行补充网页发现，结果不得进入论文去重与引文关系流程。

        参数：
            query：已由 Query Agent 校验的结构化检索意图。
        返回：
            list[SupplementalDiscoveryItem]：显式标记不可作为论文合并的网页结果。
        异常：
            RuntimeError：来源不可用、限流或响应结构异常时抛出已净化错误。
        """
        ...  # Protocol 仅声明补充发现边界，不实现 HTTP、鉴权或响应解析。
