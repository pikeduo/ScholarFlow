"""根据覆盖缺口生成去重且不放宽硬约束的下一轮子查询。"""

import hashlib  # 为精确重复查询生成稳定指纹。
import re  # 提取大小写无关的查询词项用于近似重复判断。
from collections.abc import Sequence  # 接收已执行查询的只读序列。

from backend.app.models.coverage import CoverageGap, CoverageReport  # 读取按严重度排序的覆盖缺口。
from backend.app.models.query_evolution import QueryEvolutionResult  # 返回可直接交给控制器消费的演化结果。
from backend.app.models.query_intent import QueryIntent, QuerySubquery, SubqueryPurpose  # 构造保持既有领域契约的英文子查询。


_QUERYABLE_GAP_PURPOSES: dict[str, SubqueryPurpose] = {"must_include": "method", "method": "method", "dataset": "dataset"}  # 仅将具备明确文本焦点的缺口转换为学术检索子查询。


class QueryEvolutionService:
    """从 CoverageReport 中选择可检索缺口，并生成安全去重的英文补充查询。"""

    def __init__(self, max_queries_per_gap: int = 1, similarity_threshold: float = 0.8) -> None:
        """保存每个缺口的生成上限与词项 Jaccard 相似度拒绝阈值。

        参数：
            max_queries_per_gap：每个覆盖缺口最多生成的补充查询数量。
            similarity_threshold：与已知查询相似到此阈值时拒绝生成的比例。
        异常：
            ValueError：数量上限或相似度阈值超出有效范围时抛出。
        """
        if max_queries_per_gap < 1:  # 零上限会让服务表面运行却永远不能修复任何缺口。
            raise ValueError("max_queries_per_gap 必须大于零")  # 在装配阶段暴露稳定的配置错误。
        if not 0.0 < similarity_threshold <= 1.0:  # 阈值必须能区分不同查询且允许精确重复拦截。
            raise ValueError("similarity_threshold 必须位于 (0, 1] 区间")  # 防止调用方传入无法解释的相似度策略。
        self._max_queries_per_gap = max_queries_per_gap  # 保存每个缺口的独立查询预算。
        self._similarity_threshold = similarity_threshold  # 保存大小写无关词项相似度阈值。

    def evolve(
        self,
        query: QueryIntent,
        coverage_report: CoverageReport,
        *,
        executed_subqueries: Sequence[str] = (),
    ) -> QueryEvolutionResult:
        """针对可查询缺口生成补充子查询，并拒绝重复或近似重复内容。

        参数：
            query：本轮实际使用的结构化查询意图。
            coverage_report：覆盖分析产生的按优先级排序的缺口报告。
            executed_subqueries：本次运行此前已执行的查询文本，用于跨轮去重。
        返回：
            QueryEvolutionResult：含新子查询及更新后 QueryIntent 副本的稳定结果。
        """
        known_queries = [*executed_subqueries, *(subquery.query for subquery in query.subqueries)]  # 同时防止跨轮重复和意图内重复。
        known_fingerprints = {_query_fingerprint(item) for item in known_queries if item.strip()}  # 将已有查询转为精确重复检查集合。
        known_token_sets = [_query_tokens(item) for item in known_queries if item.strip()]  # 保留词项集合以检测语义近似的机械重复。
        generated_subqueries: list[QuerySubquery] = []  # 收集本次通过全部安全检查的新增子查询。
        warnings: list[str] = []  # 收集安全可展示的跳过原因而不包含完整原始查询。
        skipped_gap_count = 0  # 统计每个未生成补充查询的覆盖缺口。
        for gap in coverage_report.gaps:  # 按覆盖分析已确定的严重度顺序逐个处理缺口。
            purpose = _QUERYABLE_GAP_PURPOSES.get(gap.gap_type)  # 只为可由文本查询修复的缺口选择子查询用途。
            if purpose is None:  # 来源、年份和数量问题需要控制器或路由器处理而非伪造检索词。
                skipped_gap_count += 1  # 记录该缺口没有产生新的文本检索查询。
                warnings.append(f"缺口“{gap.constraint}”需由后续搜索控制器处理")  # 返回不含内部实现细节的稳定说明。
                continue  # 继续分析下一个可查询缺口。
            generated_for_gap = 0  # 记录当前缺口已成功生成的查询数量。
            for candidate_query in self._candidate_queries(query, gap):  # 生成确定性的候选而不调用 LLM 或外部服务。
                if generated_for_gap >= self._max_queries_per_gap:  # 遵守每个缺口独立的生成预算。
                    break  # 不为同一缺口无限扩展同义或排列组合查询。
                fingerprint = _query_fingerprint(candidate_query)  # 计算候选的顺序和大小写无关精确指纹。
                token_set = _query_tokens(candidate_query)  # 提取候选词项用于近似重复检测。
                if fingerprint in known_fingerprints or _is_too_similar(token_set, known_token_sets, self._similarity_threshold):  # 拒绝已执行、已计划或过度相似的子查询。
                    continue  # 尝试同一缺口的下一个确定性表达。
                generated_subquery = QuerySubquery(query=candidate_query, language="en", purpose=purpose)  # 构造能直接传递给来源适配器的英文子查询。
                generated_subqueries.append(generated_subquery)  # 保留生成顺序以匹配缺口优先级。
                known_fingerprints.add(fingerprint)  # 让同一轮后续缺口也不能重复使用该表达。
                known_token_sets.append(token_set)  # 让相似度判断覆盖本次刚生成的查询。
                generated_for_gap += 1  # 更新当前缺口的生成数量。
            if generated_for_gap == 0:  # 所有候选均与已知查询重复或近似时不应重复调用来源。
                skipped_gap_count += 1  # 记录该缺口未得到新的可执行查询。
                warnings.append(f"缺口“{gap.constraint}”没有可执行的新查询")  # 向控制器说明应考虑停止而不是重试同一查询。
        updated_query = query.model_copy(update={"subqueries": [*query.subqueries, *generated_subqueries]})  # 仅追加新查询，原始硬约束和排除词保持不变。
        return QueryEvolutionResult(  # 返回纯数据结果，不自行触发任何外部检索。
            query_intent=updated_query,
            generated_subqueries=generated_subqueries,
            skipped_gap_count=skipped_gap_count,
            warnings=warnings,
        )

    def _candidate_queries(self, query: QueryIntent, gap: CoverageGap) -> list[str]:
        """为单个可查询缺口构造有限、确定且英文优先的候选表达。"""
        base_terms = _distinct_terms([*query.research_topics, *query.tasks, *query.methods, *query.datasets])  # 优先使用已结构化的英文领域词而非原始用户全文。
        base_query = " ".join(base_terms) or query.normalized_query.strip()  # 结构化词缺失时回退到原有英文规范化查询。
        focus = gap.recommended_query_focus.strip()  # 使用覆盖报告提供的具体缺口焦点。
        candidates = [" ".join(_distinct_terms([*base_terms, focus])) if base_terms else " ".join(part for part in (base_query, focus) if part)]  # 首选在原有研究上下文中补足缺口关键词并避免重复拼接焦点词。
        if gap.gap_type == "dataset":  # 数据集缺口可使用任务和数据集组合得到不同于通用主题的表达。
            task_terms = _distinct_terms([*query.tasks, *query.research_topics])  # 保留任务优先的简洁语境。
            candidates.append(" ".join(_distinct_terms([focus, *task_terms])))  # 生成以数据集开头且不重复术语的候选以提升来源全文匹配机会。
        if gap.gap_type in {"method", "must_include"}:  # 方法或必含词缺口可结合数据集和任务形成另一种上下文。
            context_terms = _distinct_terms([*query.datasets, *query.tasks, *query.research_topics])  # 不移除任何硬约束，只添加已有语义条件。
            candidates.append(" ".join(_distinct_terms([focus, *context_terms])))  # 生成以缺口词开头且不重复术语的确定性替代顺序。
        return _distinct_queries(candidates)  # 防止同一缺口的两个模板恰好生成相同文本。


def _distinct_terms(terms: Sequence[str]) -> list[str]:
    """大小写无关去重术语并保留首次出现的展示形式。"""
    result: list[str] = []  # 保存可用于构造查询的有序术语。
    seen: set[str] = set()  # 记录规范化术语以避免重复拼接。
    for term in terms:  # 逐项处理结构化语义字段。
        normalized = " ".join(term.split())  # 合并多余空白避免产生不同指纹的等价表达。
        key = normalized.casefold()  # 使用大小写无关键进行去重。
        if normalized and key not in seen:  # 仅保留首次有效术语。
            result.append(normalized)  # 保存原始大小写供来源检索与前端展示。
            seen.add(key)  # 标记该术语已经参与构造。
    return result  # 返回稳定有序的独特术语列表。


def _distinct_queries(queries: Sequence[str]) -> list[str]:
    """通过稳定指纹去除同一缺口内部的等价候选查询。"""
    result: list[str] = []  # 保存可交给全局重复检查的候选列表。
    seen: set[str] = set()  # 记录本缺口已经出现的精确查询指纹。
    for query in queries:  # 逐条处理模板生成的候选。
        normalized = " ".join(query.split())  # 清理多余空白以保证输出可执行。
        fingerprint = _query_fingerprint(normalized)  # 计算大小写和词序无关的稳定指纹。
        if normalized and fingerprint not in seen:  # 仅保留第一条有效等价表达。
            result.append(normalized)  # 保存规范化后的来源查询文本。
            seen.add(fingerprint)  # 标记当前缺口已使用该表达。
    return result  # 返回有限且不重复的候选。


def _query_fingerprint(query: str) -> str:
    """生成对空白、大小写和词序稳定的查询指纹。"""
    canonical = " ".join(sorted(_query_tokens(query)))  # 仅保留规范词项，避免格式差异绕过去重。
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()  # 使用 UTF-8 生成可跨平台复现的哈希值。


def _query_tokens(query: str) -> set[str]:
    """提取大小写无关的 Unicode 词项集合以进行 Jaccard 相似度比较。"""
    return set(re.findall(r"[^\W_]+", query.casefold(), flags=re.UNICODE))  # 保留中文、英文和数字词项并丢弃标点与下划线。


def _is_too_similar(candidate_tokens: set[str], known_token_sets: Sequence[set[str]], threshold: float) -> bool:
    """判断候选与任一已知查询的词项 Jaccard 相似度是否达到拒绝阈值。"""
    if not candidate_tokens:  # 空词项候选无法形成有意义的学术检索请求。
        return True  # 保守拒绝而不是生成空查询。
    for known_tokens in known_token_sets:  # 逐个比较所有已执行、已有和本轮新增查询。
        union = candidate_tokens | known_tokens  # 计算两个查询词项的并集。
        if union and len(candidate_tokens & known_tokens) / len(union) >= threshold:  # 达到阈值即视为过度相似。
            return True  # 拒绝避免重复调用同一来源。
    return False  # 没有任何已知查询过度相似时允许生成。
