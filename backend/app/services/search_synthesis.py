"""从 SQLite 已保存的多轮检索结果构建不调用模型的综合报告。"""

from collections import Counter  # 按关键词统计不同论文的来源事实频次。

from backend.app.models.multi_round_search import MultiRoundSearchResult  # 读取同次完成结果快照。
from backend.app.models.paper import PaperRecord, PaperSource  # 使用已规范化论文的公开元数据。
from backend.app.models.search_synthesis import SearchSynthesisKeyword, SearchSynthesisReport, SearchSynthesisSource  # 返回稳定且可前端消费的报告契约。


class SearchSynthesisService:
    """只根据已保存运行事实汇总检索结论、覆盖不足和后续建议。"""

    def build(self, result: MultiRoundSearchResult) -> SearchSynthesisReport:
        """从完整结果快照构造一次可重复、无副作用的综合报告。

        参数：
            result：按运行标识从 SQLite 恢复的完成结果快照。
        返回：
            SearchSynthesisReport：只使用已保存论文、覆盖和停止状态的事实型报告。
        """
        papers = result.papers  # 仅读取同次最终论文，不访问外部来源或全文。
        coverage = result.coverage_report or result.run_state.coverage_report  # 优先使用工作流已保存的最终覆盖判断。
        high_relevance_count = coverage.high_relevance_count if coverage is not None else sum(paper.constraint_status == "satisfied" for paper in papers)  # 缺少历史覆盖报告时使用已保存核验状态保守回退。
        partial_relevance_count = coverage.partial_relevance_count if coverage is not None else sum(paper.constraint_status == "uncertain" for paper in papers)  # 保持待确认候选与高相关结论区分。
        not_satisfied_count = sum(paper.constraint_status == "not_satisfied" for paper in papers)  # 单独统计未通过约束核验的保留候选。
        years = [paper.year for paper in papers if paper.year is not None]  # 只统计来源明确提供的发表年份。
        sources = self._build_sources(result, papers)  # 汇总来源召回与最终主记录数量。
        top_keywords = self._build_top_keywords(papers)  # 聚合来源提供关键词，不从标题或摘要猜测。
        gaps = coverage.gaps if coverage is not None else []  # 未保存覆盖报告时返回空缺口而非重新生成推断。
        findings = self._build_findings(len(papers), high_relevance_count, partial_relevance_count, years, sources)  # 以模板化文字解释事实统计。
        suggestions = self._build_suggestions(gaps, result.run_state.stop_reason)  # 仅针对已知缺口给出可操作建议。
        return SearchSynthesisReport(  # 返回不写入数据库且可重复生成的报告。
            run_id=result.run_state.run_id,
            final_paper_count=len(papers),
            high_relevance_count=high_relevance_count,
            partial_relevance_count=partial_relevance_count,
            not_satisfied_count=not_satisfied_count,
            year_start=min(years) if years else None,
            year_end=max(years) if years else None,
            sources=sources,
            top_keywords=top_keywords,
            coverage_gaps=gaps,
            stop_reason=result.run_state.stop_reason or (coverage.stop_reason if coverage is not None else None),
            findings=findings,
            follow_up_suggestions=suggestions,
        )

    @staticmethod
    def _build_sources(result: MultiRoundSearchResult, papers: list[PaperRecord]) -> list[SearchSynthesisSource]:
        """按实际参与顺序汇总来源召回和最终论文数量。"""
        final_counts = Counter(paper.source for paper in papers)  # 按最终规范化记录的主来源计数。
        source_order = [*result.run_state.selected_sources]  # 优先使用工作流真实参与来源顺序。
        for source_name in result.source_counts:  # 兼容历史快照只有来源统计的场景。
            if source_name not in source_order:  # 避免同一来源在报告中重复出现。
                source_order.append(source_name)  # 追加来源统计中的稳定键。
        for source_name in final_counts:  # 确保最终论文来源即使无召回统计也会展示。
            if source_name not in source_order:  # 防御旧快照字段不完整。
                source_order.append(source_name)  # 使用论文实际主来源补全顺序。
        return [SearchSynthesisSource(source=source_name, recalled_count=result.source_counts.get(source_name, 0), final_paper_count=final_counts.get(source_name, 0)) for source_name in source_order]  # 仅投影已保存来源事实。

    @staticmethod
    def _build_top_keywords(papers: list[PaperRecord]) -> list[SearchSynthesisKeyword]:
        """按论文去重后统计来源关键词，并返回稳定的前八项。"""
        keyword_counts: dict[str, tuple[str, int]] = {}  # 保存规范化键、首个展示词和不同论文次数。
        for paper in papers:  # 逐篇收集来源关键词。
            unique_keywords = {keyword.strip() for keyword in paper.keywords if keyword.strip()}  # 防止同一论文的重复关键词放大频次。
            for keyword in unique_keywords:  # 每个论文内的有效关键词只计一次。
                normalized = keyword.casefold()  # 合并英文关键词的大小写差异。
                display_keyword, count = keyword_counts.get(normalized, (keyword, 0))  # 保留首次来源展示词。
                keyword_counts[normalized] = (display_keyword, count + 1)  # 增加对应论文覆盖数。
        ordered_keywords = sorted(keyword_counts.values(), key=lambda item: (-item[1], item[0].casefold()))[:8]  # 使用频次和词形保证稳定前八排序。
        return [SearchSynthesisKeyword(keyword=keyword, paper_count=count) for keyword, count in ordered_keywords]  # 转换为公开响应契约。

    @staticmethod
    def _build_findings(final_paper_count: int, high_relevance_count: int, partial_relevance_count: int, years: list[int], sources: list[SearchSynthesisSource]) -> list[str]:
        """基于已保存数字构造不含模型推断的简短结论。"""
        findings = [f"本次最终保留 {final_paper_count} 篇论文，其中 {high_relevance_count} 篇已满足关键约束。"]  # 始终说明最终结果与高相关数量。
        if partial_relevance_count:  # 仅在存在待确认候选时提示用户人工复核。
            findings.append(f"另有 {partial_relevance_count} 篇论文处于待确认状态，建议结合摘要和条件证据复核。")  # 不将待确认论文描述为已满足。
        if years:  # 年份范围仅由来源已有年份支持。
            findings.append(f"可确认发表年份覆盖 {min(years)} 至 {max(years)}。")  # 说明结果时间分布范围。
        if sources:  # 仅在来源统计可用时说明来源范围。
            findings.append(f"本次结果由 {len(sources)} 个已参与学术来源提供候选。")  # 不推断来源间质量高低。
        return findings[:5]  # 保持页面报告紧凑可扫读。

    @staticmethod
    def _build_suggestions(gaps: list, stop_reason: str | None) -> list[str]:
        """将既有覆盖缺口映射为不改变硬约束的后续操作建议。"""
        suggestions = [f"可围绕“{gap.constraint}”补充检索，优先处理{gap.gap_type}覆盖缺口。" for gap in gaps[:3]]  # 只回显工作流已经识别的缺口。
        if not suggestions and stop_reason:  # 没有缺口时仅说明本轮已经停止，不虚构新的研究方向。
            suggestions.append(f"本次搜索已停止：{stop_reason}。可通过调整明确条件后发起新的搜索运行。")  # 将操作边界留给用户控制。
        return suggestions[:5]  # 限制页面展示的建议数量。
