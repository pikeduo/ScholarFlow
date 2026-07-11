"""编排自然语言学术查询解析并记录安全统计。"""

from backend.app.adapters.deepseek_query_planner import DeepSeekQueryPlanningClient, QueryPlanningClient  # 依赖可替换查询规划协议。
from backend.app.core.logging import logger  # 记录不含完整用户查询的规划统计。
from backend.app.models.natural_search import NaturalSearchRequest  # 接收自然语言请求。
from backend.app.models.query_intent import QueryIntent  # 返回完整下游契约。


class QueryPlanningService:
    """将自然语言请求转换为结构化、英文可检索的 QueryIntent。"""

    def __init__(self, client: QueryPlanningClient | None = None) -> None:
        """保存可替换规划客户端。"""
        self._client = client or DeepSeekQueryPlanningClient()  # 默认使用 DeepSeek，测试可注入替身。

    async def plan(self, request: NaturalSearchRequest) -> QueryIntent:
        """执行查询规划并记录字段数量而不记录用户原文。"""
        intent = await self._client.plan(request)  # 由适配层完成外部调用和结构校验。
        logger.info("查询规划完成：主题=%d，方法=%d，任务=%d，数据集=%d，子查询=%d，来源召回上限=%d", len(intent.research_topics), len(intent.methods), len(intent.tasks), len(intent.datasets), len(intent.subqueries), intent.source_recall_count or intent.target_paper_count)  # 仅记录数量统计。
        return intent  # 返回可直接进入多源协调器的稳定计划。
