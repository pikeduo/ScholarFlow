"""协调已路由的学术来源与补充网页来源，并隔离单源故障。"""

import asyncio  # 并发执行互不依赖的来源调用。
from collections.abc import Mapping  # 接收可替换的来源适配器注册表。

from backend.app.adapters.base import AcademicSearchAdapter, WebDiscoveryAdapter  # 仅依赖来源无关的适配器协议。
from backend.app.core.logging import logger  # 记录来源级成功、降级与统计信息。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 区分不可合并的网页发现结果。
from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 构造协调阶段统一输出。
from backend.app.models.paper import PaperRecord  # 保存进入后续规范化阶段的论文记录。
from backend.app.models.query_intent import QueryIntent  # 接收已完成查询规划的统一意图。
from backend.app.services.multi_source_filtering import MultiSourcePaperFilter  # 在语义排序前应用多源确定性规则过滤。
from backend.app.services.paper_fusion import PaperFusionService  # 在协调器边界执行跨来源身份融合与 RRF 计算。
from backend.app.services.source_router import SourceRouter  # 使用确定性来源路由规则生成执行计划。


class MultiSourceRecallCoordinator:
    """执行多源召回、论文融合、网页补充发现和单源故障降级。

    参数：
        source_router：生成本次查询来源计划的可替换路由器。
        academic_adapters：按来源名称注册的学术搜索适配器。
        web_discovery_adapters：按来源名称注册的补充网页发现适配器。
        paper_fusion_service：可替换的跨来源身份融合和 RRF 服务。
        paper_filter：可替换的融合论文确定性规则过滤服务。
    """

    def __init__(
        self,
        source_router: SourceRouter,
        academic_adapters: Mapping[str, AcademicSearchAdapter],
        web_discovery_adapters: Mapping[str, WebDiscoveryAdapter] | None = None,
        paper_fusion_service: PaperFusionService | None = None,
        paper_filter: MultiSourcePaperFilter | None = None,
    ) -> None:
        """保存路由器和只读适配器注册表，避免协调器绑定具体供应商实现。"""
        self._source_router = source_router  # 保存可测试的来源路由策略。
        self._academic_adapters = dict(academic_adapters)  # 复制注册表避免调用期间外部修改来源映射。
        self._web_discovery_adapters = dict(web_discovery_adapters or {})  # 保存可选网页发现注册表并默认空映射。
        self._paper_fusion_service = paper_fusion_service or PaperFusionService()  # 默认使用统一融合策略并允许测试替换。
        self._paper_filter = paper_filter or MultiSourcePaperFilter()  # 默认在排序前应用 QueryIntent 硬约束过滤。

    async def recall(self, query: QueryIntent) -> MultiSourceRecallResult:
        """按路由计划并发召回学术论文和补充网页发现项。

        参数：
            query：已由 Query Agent 校验的完整检索意图。
        返回：
            MultiSourceRecallResult：融合论文、独立网页发现、来源数量与降级错误。
        """
        route_plan = self._source_router.route(query)  # 在发起任何来源调用前生成可审计执行计划。
        academic_tasks = [  # 为每个已选学术来源构造独立且可降级的协程。
            self._recall_academic_source(source_name, query)  # 不让单一来源异常传播并取消其他来源调用。
            for source_name in route_plan.academic_sources  # 按计划固定顺序保留来源结果拼接顺序。
        ]
        discovery_tasks = [  # 为每个已选网页发现来源构造独立且可降级的协程。
            self._recall_web_discovery_source(source_name, query)  # 保持网页发现与论文召回的错误边界分离。
            for source_name in route_plan.web_discovery_sources  # 仅执行路由器已显式批准的补充来源。
        ]
        academic_outcomes, discovery_outcomes = await asyncio.gather(  # 并行启动两类来源任务以减少端到端等待时间。
            asyncio.gather(*academic_tasks),  # 等待所有学术来源完成或各自降级。
            asyncio.gather(*discovery_tasks),  # 等待所有网页发现来源完成或各自降级。
        )
        recalled_papers: list[PaperRecord] = []  # 按来源计划顺序汇总尚未跨来源融合的论文记录。
        discoveries: list[SupplementalDiscoveryItem] = []  # 汇总永不进入论文融合的网页发现项。
        source_counts: dict[str, int] = {}  # 保存每个来源成功返回的条目数量。
        source_errors: dict[str, str] = {}  # 保存安全可展示的来源降级错误摘要。
        for source_name, source_papers, error_message in academic_outcomes:  # 按计划顺序处理学术来源执行结果。
            source_counts[source_name] = len(source_papers)  # 无结果或降级时明确记录零，便于运行统计。
            recalled_papers.extend(source_papers)  # 仅将成功映射的论文交给统一融合服务。
            if error_message is not None:  # 单源失败不阻断整体召回，但必须可审计。
                source_errors[source_name] = error_message  # 保存不含底层异常和请求信息的稳定错误摘要。
        for source_name, source_discoveries, error_message in discovery_outcomes:  # 按计划顺序处理网页来源执行结果。
            source_counts[source_name] = len(source_discoveries)  # 独立记录网页发现数量而不计入论文召回数。
            discoveries.extend(source_discoveries)  # 保持补充网页结果独立于论文集合。
            if error_message is not None:  # 网页来源故障也允许整体学术检索继续。
                source_errors[source_name] = error_message  # 保存安全可展示的网页来源错误摘要。
        fusion_result = self._paper_fusion_service.fuse(recalled_papers)  # 在 API 边界前统一执行身份解析、字段融合、版本族与 RRF。
        filter_result = self._paper_filter.filter(fusion_result.papers, query)  # 在进入语义排序前应用可解释的确定性规则过滤。
        logger.info("多源召回完成：原始论文=%d，融合论文=%d，过滤=%d，最终候选=%d，网页发现=%d，来源错误=%d", fusion_result.input_count, fusion_result.fused_count, filter_result.filtered_count, len(filter_result.papers), len(discoveries), len(source_errors))  # 记录不含完整查询、密钥和响应正文的阶段统计。
        return MultiSourceRecallResult(  # 构造供后续规范化、去重与运行状态更新使用的结果。
            route_plan=route_plan,  # 保留本轮真实执行的来源选择计划。
            papers=filter_result.papers,  # 返回已融合且通过确定性过滤的论文记录。
            discoveries=discoveries,  # 返回不可合并的补充网页发现项。
            source_counts=source_counts,  # 返回每个已选来源的成功结果数。
            source_errors=source_errors,  # 返回来源级安全降级错误摘要。
            raw_paper_count=fusion_result.input_count,  # 返回融合前的原始学术论文数量。
            merged_paper_count=fusion_result.merged_count,  # 返回被身份融合合并的重复记录数量。
            filtered_paper_count=filter_result.filtered_count,  # 返回融合后被规则过滤移除的论文数量。
            filter_reason_counts=filter_result.filter_reason_counts,  # 返回按首个失败规则汇总的过滤统计。
            work_family_count=filter_result.work_family_count,  # 返回最终候选中可识别版本族的唯一数量。
        )

    async def _recall_academic_source(
        self,
        source_name: str,
        query: QueryIntent,
    ) -> tuple[str, list[PaperRecord], str | None]:
        """执行单个学术来源并将异常隔离为稳定降级结果。"""
        adapter = self._academic_adapters.get(source_name)  # 从注册表读取路由器选中的来源适配器。
        if adapter is None:  # 配置与适配器注册不一致时不应让整个工作流崩溃。
            logger.error("学术来源未注册：来源=%s", source_name)  # 记录可定位但不含查询的配置错误。
            return source_name, [], "学术来源适配器未注册"  # 返回稳定错误并允许其余来源继续。
        try:  # 单个来源网络、限流或映射失败时执行隔离降级。
            papers = await adapter.search(query)  # 调用统一学术协议，不依赖供应商字段。
        except Exception:  # 来源适配器已负责转换底层异常，此处仅负责流程隔离。
            logger.exception("学术来源召回失败：来源=%s", source_name)  # 记录完整堆栈到受控日志而不返回给调用方。
            return source_name, [], "学术来源调用失败"  # 返回不含内部路径、密钥或响应正文的稳定摘要。
        logger.info("学术来源召回成功：来源=%s，论文数=%d", source_name, len(papers))  # 记录来源级成功统计。
        return source_name, papers, None  # 返回成功论文供协调器按计划顺序汇总。

    async def _recall_web_discovery_source(
        self,
        source_name: str,
        query: QueryIntent,
    ) -> tuple[str, list[SupplementalDiscoveryItem], str | None]:
        """执行单个补充网页来源并将异常隔离为稳定降级结果。"""
        adapter = self._web_discovery_adapters.get(source_name)  # 从注册表读取路由器选中的补充来源适配器。
        if adapter is None:  # 配置与网页适配器注册不一致时不应阻断论文召回。
            logger.error("补充网页来源未注册：来源=%s", source_name)  # 记录可定位但不含查询的配置错误。
            return source_name, [], "补充网页来源适配器未注册"  # 返回稳定错误并保持与论文来源独立。
        try:  # 网页来源的认证、网络或映射失败不得影响学术来源。
            discoveries = await adapter.discover(query)  # 调用独立补充发现协议，不将结果视为论文。
        except Exception:  # 来源适配器已负责转换底层异常，此处仅负责流程隔离。
            logger.exception("补充网页来源调用失败：来源=%s", source_name)  # 记录完整堆栈到受控日志而不返回给调用方。
            return source_name, [], "补充网页来源调用失败"  # 返回不含内部路径、密钥或响应正文的稳定摘要。
        logger.info("补充网页来源调用成功：来源=%s，结果数=%d", source_name, len(discoveries))  # 记录来源级成功统计。
        return source_name, discoveries, None  # 返回永不混入论文集合的补充发现项。
