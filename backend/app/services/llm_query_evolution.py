"""将 LLM 策略提案与既有确定性查询演化安全组合。"""

from backend.app.adapters.deepseek_search_strategy import DeepSeekSearchStrategyClient, SearchStrategyClient, SearchStrategyError  # 隔离供应商调用与可替换协议。
from backend.app.core.logging import logger  # 记录数量、Token 和安全降级而不记录查询正文。
from backend.app.models.coverage import CoverageReport  # 接收已验证覆盖缺口。
from backend.app.models.paper import PaperRecord  # 仅向策略客户端传递当前已保存候选。
from backend.app.models.query_evolution import QueryEvolutionResult  # 返回既有工作流可消费的稳定结果。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 复用统一查询契约。
from backend.app.services.query_evolution import QueryEvolutionService, _is_too_similar, _query_fingerprint, _query_tokens  # 复用既有去重规则避免策略绕过成本保护。


class LlmQueryEvolutionService:
    """优先采用 LLM 的下一轮子查询建议，失败时无缝回退本地演化规则。"""

    def __init__(self, client: SearchStrategyClient | None = None, fallback: QueryEvolutionService | None = None, *, enabled: bool = True) -> None:
        """保存可替换策略客户端、确定性回退服务和是否允许外部模型调用的开关。"""
        self._client = client or DeepSeekSearchStrategyClient()  # 默认在覆盖缺口存在时调用一次低成本策略模型。
        self._fallback = fallback or QueryEvolutionService()  # 任意模型故障均保留原有可执行演化。
        self._enabled = enabled  # 离线测试与显式降级场景不得意外调用外部模型。

    async def evolve(self, query: QueryIntent, coverage_report: CoverageReport, *, papers: list[PaperRecord], executed_subqueries: list[str]) -> QueryEvolutionResult:
        """根据当前论文证据请求策略提案，并保证硬约束、去重和成本保护不变。"""
        fallback_result = self._fallback.evolve(query, coverage_report, executed_subqueries=executed_subqueries)  # 先计算无网络的安全回退结果。
        if not self._enabled or not coverage_report.gaps:  # 显式关闭或没有缺口时不应额外消耗模型调用。
            return fallback_result  # 保持原有空缺口行为。
        try:  # LLM 失败不得阻断后续确定性查询演化。
            proposal = await self._client.propose(query, coverage_report, papers, executed_subqueries)  # 仅在仍需继续搜索时调用一次策略模型。
        except SearchStrategyError:  # 密钥、网络或 JSON 异常统一回退。
            logger.exception("LLM 搜索策略降级：候选数=%d，缺口数=%d", len(papers), len(coverage_report.gaps))  # 只记录非敏感计数与完整受控堆栈。
            return fallback_result.model_copy(update={"warnings": [*fallback_result.warnings, "LLM 搜索策略不可用，已使用规则化查询演化"]})  # 明确向运行状态公开降级而非伪装模型成功。
        approved = self._approve_subqueries(query, proposal.subqueries, executed_subqueries)  # 让模型提案仍受既有重复与语言保护。
        generated = approved or fallback_result.generated_subqueries  # 仅在模型没有任何可执行表达时回退规则化演化。
        updated_intent = query.model_copy(update={"subqueries": [*query.subqueries, *generated]})  # 不修改输入并保留原硬约束和计划。
        warnings = fallback_result.warnings if approved else [*fallback_result.warnings, "LLM 搜索策略未生成可执行新查询，已使用规则化查询演化"]  # 让用户可区分模型建议与回退。
        logger.info("LLM 搜索策略完成：提案=%d，可执行=%d，候选数=%d，输入Token=%d，输出Token=%d", len(proposal.subqueries), len(approved), len(papers), proposal.prompt_tokens, proposal.completion_tokens)  # 记录可审计成本统计而不记录论文或查询正文。
        return QueryEvolutionResult(query_intent=updated_intent, generated_subqueries=generated, skipped_gap_count=fallback_result.skipped_gap_count, warnings=warnings, strategy_reason=proposal.reason or None, strategy_model_name=proposal.model_name, strategy_prompt_tokens=proposal.prompt_tokens, strategy_completion_tokens=proposal.completion_tokens)  # 返回兼容既有工作流并补充策略审计信息。

    @staticmethod
    def _approve_subqueries(query: QueryIntent, proposals: list[QuerySubquery], executed_subqueries: list[str]) -> list[QuerySubquery]:
        """拒绝非英文、重复或过度相似的模型提案，最多保留两条。"""
        known_queries = [*executed_subqueries, *(item.query for item in query.subqueries)]  # 合并跨轮已执行和原计划查询。
        fingerprints = {_query_fingerprint(item) for item in known_queries if item.strip()}  # 构造精确去重集合。
        token_sets = [_query_tokens(item) for item in known_queries if item.strip()]  # 构造相似度保护集合。
        approved: list[QuerySubquery] = []  # 保存顺序稳定的有效提案。
        for proposal in proposals[:2]:  # 强制限制策略模型单轮最多影响两条查询。
            normalized = " ".join(proposal.query.split())  # 规范化空白避免等价表达绕过去重。
            if proposal.language != "en" or not normalized or len(normalized) > 300:  # 仅允许短英文来源查询。
                continue  # 非法表达不能进入来源调用。
            fingerprint = _query_fingerprint(normalized)  # 计算顺序无关的稳定指纹。
            tokens = _query_tokens(normalized)  # 提取词项集合用于近似重复判断。
            if fingerprint in fingerprints or _is_too_similar(tokens, token_sets, 0.8):  # 复用既有相似度阈值保护来源预算。
                continue  # 重复或过度相似表达不应重新检索。
            approved.append(proposal.model_copy(update={"query": normalized}))  # 保留原合法 purpose 并写入规范化文本。
            fingerprints.add(fingerprint)  # 防止同一提案批内重复。
            token_sets.append(tokens)  # 让后续提案也受本批相似度检查。
        return approved  # 返回完全受控的模型策略结果。
