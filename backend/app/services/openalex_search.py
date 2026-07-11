"""编排 OpenAlex 单数据源检索、去重和阶段统计。"""

from collections.abc import Callable  # 声明可注入的去重函数类型。
from typing import Protocol  # 定义可替换的 OpenAlex 客户端边界。

from backend.app.core.logging import logger  # 记录不含查询内容的检索阶段统计。
from backend.app.models.paper import Paper  # 使用统一论文模型贯通服务边界。
from backend.app.models.query import QuerySchema  # 接收已校验的结构化检索约束。
from backend.app.models.search import SearchResult  # 返回稳定的检索阶段输出模型。
from backend.app.services.deduplication import deduplicate_papers  # 复用统一的论文去重规则。
from backend.app.services.filtering import filter_papers  # 在排序前应用本地确定性约束。


class OpenAlexPaperClient(Protocol):
    """约束 OpenAlex 检索客户端所需的最小能力。

    任何实现只要能接受 QuerySchema 并异步返回统一论文列表，即可注入服务，
    因此单元测试无需访问网络，也不依赖真实 API 密钥。
    """

    async def search_works(self, query: QuerySchema) -> list[Paper]:
        """按结构化查询返回已完成映射的 OpenAlex 论文。

        参数：
            query：已校验的结构化查询约束。
        返回：
            list[Paper]：客户端成功映射的原始论文列表。
        异常：
            RuntimeError：客户端不可用时由适配层抛出的已净化错误。
        """
        ...  # Protocol 仅声明调用契约，不提供具体实现。


class OpenAlexSearchService:
    """完成 OpenAlex 单轮检索的业务编排。

    参数：
        client：已封装 HTTP、鉴权和响应映射的 OpenAlex 客户端。
        deduplicator：可替换的论文去重函数，默认采用统一去重策略。
    """

    def __init__(
        self,
        client: OpenAlexPaperClient,
        deduplicator: Callable[[list[Paper]], list[Paper]] = deduplicate_papers,
    ) -> None:
        """保存外部客户端和可测试的去重策略。"""
        self._client = client  # 保持 HTTP 适配层与业务服务解耦。
        self._deduplicator = deduplicator  # 允许测试替换或未来扩展去重策略。

    async def search(self, query: QuerySchema) -> SearchResult:
        """执行一次 OpenAlex 召回并返回去重后的统计结果。

        参数：
            query：已由调用方或 Query Agent 校验的查询约束。
        返回：
            SearchResult：包含去重论文与前后数量统计的结果。
        异常：
            RuntimeError：客户端调用失败时原样传递其已净化异常。
        """
        recalled_papers = await self._client.search_works(query)  # 委托适配层完成安全的 API 调用和字段映射。
        deduplicated_papers = self._deduplicator(recalled_papers)  # 在进入排序前执行统一的稳定标识去重。
        filtered_papers = filter_papers(deduplicated_papers, query)  # 按年份、venue、必须包含词和排除词移除不符合条件的候选。
        result = SearchResult(  # 构造供后续排序和 API 层复用的稳定输出。
            papers=filtered_papers,  # 返回保持原始相对顺序的规则过滤论文。
            recalled_count=len(recalled_papers),  # 记录客户端实际返回的规范化论文数量。
            deduplicated_count=len(deduplicated_papers),  # 记录去重后进入下一阶段的论文数量。
            filtered_count=len(deduplicated_papers) - len(filtered_papers),  # 记录被本地规则移除的论文数量。
        )
        logger.info(  # 仅输出数量统计，避免日志记录完整用户查询或论文内容。
            "OpenAlex 搜索服务完成：召回=%d，去重后=%d，规则过滤=%d，最终返回=%d",
            result.recalled_count,
            result.deduplicated_count,
            result.filtered_count,
            len(result.papers),
        )
        return result  # 返回本轮检索的可序列化业务结果。
