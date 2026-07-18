"""生成规则过滤后、BGE-M3 前的多源论文候选和独立网页发现结果。"""

import asyncio  # 并发执行互不依赖的学术来源和网页发现来源调用。
from collections.abc import Mapping  # 接收可替换的来源适配器注册表。

from backend.app.adapters.base import AcademicSearchAdapter, WebDiscoveryAdapter  # 仅依赖来源无关的适配器协议。
from backend.app.core.logging import logger  # 记录不包含查询正文、密钥或论文文本的安全统计。
from backend.app.models.candidate_generation import CandidateGenerationResult  # 返回排序前候选与阶段数量。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 保持网页发现不进入论文融合。
from backend.app.models.paper import PaperRecord  # 汇总来源适配器已经规范化的统一论文记录。
from backend.app.models.query_intent import QueryIntent  # 接收已完成查询规划的统一意图。
from backend.app.repositories.source_cache import begin_source_cache_usage, end_source_cache_usage  # 汇总本轮并发来源调用的有效缓存命中。
from backend.app.services.multi_source_filtering import MultiSourcePaperFilter  # 在任何模型排序前执行确定性规则过滤。
from backend.app.services.paper_fusion import PaperFusionService  # 执行跨来源身份融合、版本族关联和 RRF。
from backend.app.services.source_router import SourceRouter  # 使用确定性规则生成本轮来源执行计划。


class CandidateGenerationService:
    """执行来源路由、来源调用、身份融合、RRF 和确定性规则过滤。

    参数：
        source_router：生成本轮来源计划的可替换路由器。
        academic_adapters：按来源名称注册的学术搜索适配器。
        web_discovery_adapters：按来源名称注册的独立网页发现适配器。
        paper_fusion_service：可替换的身份融合、版本族和 RRF 服务。
        paper_filter：可替换的确定性规则过滤服务。

    该服务不依赖、构造或调用 BGE-M3、Cross Encoder、DeepSeek 和覆盖分析，
    因而可作为生产排序链与未来显式评测快照导出的共享候选边界。
    """

    def __init__(
        self,
        source_router: SourceRouter,
        academic_adapters: Mapping[str, AcademicSearchAdapter],
        web_discovery_adapters: Mapping[str, WebDiscoveryAdapter] | None = None,
        paper_fusion_service: PaperFusionService | None = None,
        paper_filter: MultiSourcePaperFilter | None = None,
    ) -> None:
        """保存来源注册表和确定性处理服务，不在构造阶段发起 I/O。"""
        self._source_router = source_router  # 保存可测试的来源路由策略。
        self._academic_adapters = dict(academic_adapters)  # 复制学术注册表避免调用期间被外部修改。
        self._web_discovery_adapters = dict(web_discovery_adapters or {})  # 复制网页注册表并默认空映射。
        self._paper_fusion_service = paper_fusion_service or PaperFusionService()  # 默认复用生产身份融合和 RRF 规则。
        self._paper_filter = paper_filter or MultiSourcePaperFilter()  # 默认复用生产 QueryIntent 硬约束过滤规则。

    async def generate(self, query: QueryIntent) -> CandidateGenerationResult:
        """生成规则过滤后、任何模型排序前的候选集合。

        参数：
            query：已完成校验且可直接交给来源适配器的结构化查询意图。
        返回：
            CandidateGenerationResult：学术候选、独立网页发现、来源降级和阶段数量。
        """
        route_plan = self._source_router.route(query)  # 在发起任何来源调用前生成可审计执行计划。
        academic_tasks = [self._recall_academic_source(source_name, query) for source_name in route_plan.academic_sources]  # 按路由顺序构造可独立降级的学术来源任务。
        discovery_tasks = [self._recall_web_discovery_source(source_name, query) for source_name in route_plan.web_discovery_sources]  # 按路由顺序构造独立网页发现任务。
        cache_usage_token = begin_source_cache_usage()  # 在并发任务启动前建立本轮独立缓存统计上下文。
        try:  # 无论来源协调是否意外失败都必须清理 ContextVar。
            academic_outcomes, discovery_outcomes = await asyncio.gather(  # 并行执行两类互不依赖的来源任务。
                asyncio.gather(*academic_tasks),  # 等待全部学术来源成功或各自降级。
                asyncio.gather(*discovery_tasks),  # 等待全部网页来源成功或各自降级。
            )
        finally:
            cache_hit_count = end_source_cache_usage(cache_usage_token)  # 读取并重置本轮有效来源缓存命中数。
        recalled_papers: list[PaperRecord] = []  # 按学术来源计划顺序汇总统一论文记录。
        discoveries: list[SupplementalDiscoveryItem] = []  # 按网页来源计划顺序汇总独立发现项。
        academic_source_counts: dict[str, int] = {}  # 保存每个学术来源成功映射的论文数量。
        web_discovery_source_counts: dict[str, int] = {}  # 保存每个网页来源成功返回的发现数量。
        academic_source_errors: dict[str, str] = {}  # 保存学术来源安全降级摘要。
        web_discovery_source_errors: dict[str, str] = {}  # 保存网页来源安全降级摘要。
        for source_name, source_papers, error_message in academic_outcomes:  # 按路由顺序处理学术来源结果。
            academic_source_counts[source_name] = len(source_papers)  # 失败或空结果也明确记录零。
            recalled_papers.extend(source_papers)  # 仅将成功映射的论文交给身份融合服务。
            if error_message is not None:  # 单源失败不阻断其他来源和确定性处理。
                academic_source_errors[source_name] = error_message  # 保存不含内部异常和请求参数的摘要。
        for source_name, source_discoveries, error_message in discovery_outcomes:  # 按路由顺序处理网页来源结果。
            web_discovery_source_counts[source_name] = len(source_discoveries)  # 失败或空结果也明确记录零。
            discoveries.extend(source_discoveries)  # 网页发现始终保持在独立集合中。
            if error_message is not None:  # 网页来源失败不能影响学术候选生成。
                web_discovery_source_errors[source_name] = error_message  # 保存安全网页来源降级摘要。
        fusion_result = self._paper_fusion_service.fuse(recalled_papers)  # 执行身份解析、字段融合、版本族和 RRF。
        filter_result = self._paper_filter.filter(fusion_result.papers, query)  # 在任何模型排序前应用确定性硬约束。
        result = CandidateGenerationResult(  # 构造可由生产排序链和未来快照导出器共同消费的内部结果。
            route_plan=route_plan,  # 保存本轮真实来源计划。
            query_intent=query,  # 冻结本轮实际执行的查询意图。
            papers=filter_result.papers,  # 返回规则过滤后、BGE-M3 前的学术候选。
            discoveries=discoveries,  # 返回独立网页发现项。
            academic_source_counts=academic_source_counts,  # 保存纯学术来源映射数量。
            web_discovery_source_counts=web_discovery_source_counts,  # 保存纯网页发现数量。
            academic_source_errors=academic_source_errors,  # 保存学术来源安全错误。
            web_discovery_source_errors=web_discovery_source_errors,  # 保存网页来源安全错误。
            cache_hit_count=cache_hit_count,  # 保存本轮来源缓存命中。
            normalized_candidate_count=fusion_result.input_count,  # 明确这是适配器已映射的 PaperRecord 数量而非供应商原始条目。
            deduplicated_candidate_count=fusion_result.fused_count,  # 保存身份融合和 RRF 后、规则过滤前数量。
            merged_candidate_count=fusion_result.merged_count,  # 保存被身份融合的重复来源记录数量。
            filtered_candidate_count=filter_result.filtered_count,  # 保存确定性规则移除数量。
            filter_reason_counts=filter_result.filter_reason_counts,  # 保存每篇移除论文的首个失败原因统计。
            work_family_count=filter_result.work_family_count,  # 保存过滤后候选的唯一版本族数量。
        )
        logger.info(  # 只记录阶段计数，不输出查询正文、论文标题或摘要。
            "排序前候选生成完成：规范化=%d，去重后=%d，合并=%d，过滤=%d，排序输入=%d，网页发现=%d，来源错误=%d",
            result.normalized_candidate_count,
            result.deduplicated_candidate_count,
            result.merged_candidate_count,
            result.filtered_candidate_count,
            len(result.papers),
            len(result.discoveries),
            len(result.source_errors),
        )
        return result  # 返回不含任何模型排序结果的候选生成边界。

    async def _recall_academic_source(self, source_name: str, query: QueryIntent) -> tuple[str, list[PaperRecord], str | None]:
        """执行单个学术来源并将异常隔离为稳定降级结果。"""
        adapter = self._academic_adapters.get(source_name)  # 从注册表读取路由器选中的学术适配器。
        if adapter is None:  # 应用装配遗漏不能让整轮候选生成崩溃。
            logger.error("学术来源未注册：来源=%s", source_name)  # 记录可定位且不含查询的配置错误。
            return source_name, [], "学术来源适配器未注册"  # 返回稳定错误并允许其余来源继续。
        try:  # 网络、限流或映射异常由当前来源独立降级。
            papers = await adapter.search(query)  # 调用统一学术协议且不依赖供应商字段。
        except Exception:  # 适配器负责底层错误映射，此处只隔离流程故障。
            logger.exception("学术来源召回失败：来源=%s", source_name)  # 将完整堆栈写入受控日志。
            return source_name, [], "学术来源调用失败"  # 向业务层返回不泄露内部细节的摘要。
        logger.info("学术来源召回成功：来源=%s，论文数=%d", source_name, len(papers))  # 记录来源级成功数量。
        return source_name, papers, None  # 返回成功论文供确定性融合处理。

    async def _recall_web_discovery_source(self, source_name: str, query: QueryIntent) -> tuple[str, list[SupplementalDiscoveryItem], str | None]:
        """执行单个补充网页来源并将异常隔离为稳定降级结果。"""
        adapter = self._web_discovery_adapters.get(source_name)  # 从注册表读取路由选中的网页发现适配器。
        if adapter is None:  # 网页适配器遗漏不应阻断学术论文候选。
            logger.error("补充网页来源未注册：来源=%s", source_name)  # 记录不含查询正文的配置错误。
            return source_name, [], "补充网页来源适配器未注册"  # 返回稳定摘要并保持来源类别分离。
        try:  # 网页来源认证、网络或映射失败时独立降级。
            discoveries = await adapter.discover(query)  # 调用独立网页发现协议。
        except Exception:  # 底层异常只进入受控日志。
            logger.exception("补充网页来源调用失败：来源=%s", source_name)  # 记录完整堆栈供诊断。
            return source_name, [], "补充网页来源调用失败"  # 返回不含路径、密钥或响应正文的摘要。
        logger.info("补充网页来源调用成功：来源=%s，结果数=%d", source_name, len(discoveries))  # 记录网页来源成功数量。
        return source_name, discoveries, None  # 返回永不混入论文集合的网页发现项。
