"""编排自然语言学术查询解析并记录安全统计。"""

from backend.app.adapters.deepseek_query_planner import DeepSeekQueryPlanningClient, QueryPlanningClient  # 依赖可替换查询规划协议。
from backend.app.core.logging import logger  # 记录不含完整用户查询的规划统计。
from backend.app.models.natural_search import NaturalSearchRequest, QueryPlanningResult  # 接收请求并返回带统计的规划结果。


class QueryPlanningService:
    """将自然语言请求转换为结构化、英文可检索的 QueryIntent。"""

    def __init__(self, client: QueryPlanningClient | None = None) -> None:
        """保存可替换规划客户端。"""
        self._client = client or DeepSeekQueryPlanningClient()  # 默认使用 DeepSeek，测试可注入替身。

    async def plan(self, request: NaturalSearchRequest) -> QueryPlanningResult:
        """执行查询规划并记录字段数量、耗时和 Token，且不记录用户原文。"""
        result = await self._client.plan(request)  # 由适配层完成外部调用、结构校验和用量提取。
        intent = result.query_intent  # 提取计划用于记录不含查询正文的聚合统计。
        logger.info("查询规划完成：主题=%d，方法=%d，任务=%d，数据集=%d，子查询=%d，来源召回上限=%d，模型=%s，输入Token=%d，输出Token=%d，耗时毫秒=%d", len(intent.research_topics), len(intent.methods), len(intent.tasks), len(intent.datasets), len(intent.subqueries), intent.source_recall_count or intent.target_paper_count, result.model_name or "unknown", result.prompt_tokens, result.completion_tokens, result.duration_ms)  # 只记录计数与模型统计。
        return result  # 返回可执行计划及前端可展示的调用统计。
