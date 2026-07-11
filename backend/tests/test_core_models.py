"""验证 ScholarWeave 四个核心领域契约的正常和边界行为。"""

import pytest  # 提供领域模型边界异常断言。
from pydantic import ValidationError  # 捕获 Pydantic 结构化校验异常。

from backend.app.models.paper import PaperRecord, PaperSourceRecord  # 构造多源论文与来源溯源记录。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 构造查询规划和子查询记录。
from backend.app.models.search import SearchResult  # 构造 API 层检索结果摘要。
from backend.app.models.search_run import SearchRunState  # 构造可恢复搜索运行状态。


def _build_query_intent() -> QueryIntent:
    """构造覆盖硬约束、软偏好和子查询的有效 QueryIntent。

    返回：
        QueryIntent：可供多个核心模型复用的合法查询意图。
    """
    return QueryIntent(  # 构造不依赖 LLM 或网络的完整查询意图。
        original_query="查找 ETT 上的时间序列预测论文",  # 保存用户原始中文查询。
        normalized_query="ETT 时间序列预测",  # 保存用于缓存和检索的规范化文本。
        query_language="zh",  # 标记原始查询为中文。
        research_topics=["时间序列预测"],  # 提供研究主题。
        methods=["Transformer"],  # 提供方法约束。
        datasets=["ETT"],  # 提供数据集硬约束。
        must_include=["forecasting"],  # 提供必须包含的检索词。
        should_include=["multivariate"],  # 提供尽量满足的偏好词。
        exclude=["survey"],  # 提供排除条件。
        subqueries=[QuerySubquery(query="Transformer forecasting ETT", language="en", purpose="dataset")],  # 提供面向数据集覆盖的英文子查询。
        target_paper_count=20,  # 指定期望最终论文数量。
        search_mode="standard",  # 指定标准模式。
        domains=["computer_science"],  # 提供动态第三源路由所需领域。
        complexity_score=0.4,  # 提供合法的复杂度评分。
    )


def test_core_models_preserve_query_provenance_and_run_statistics() -> None:
    """核心契约应关联查询意图、论文溯源、运行状态和最终结果摘要。"""
    query_intent = _build_query_intent()  # 构造可执行的查询意图。
    paper = PaperRecord(  # 构造包含多源溯源信息的统一论文。
        paper_id="paper-1",  # 提供当前兼容模型使用的稳定论文标识。
        title="Transformer Forecasting on ETT",  # 提供论文标题。
        source="openalex",  # 标记当前规范化论文的主来源。
        openalex_id="W1",  # 提供 OpenAlex 来源标识。
        semantic_scholar_id="S1",  # 提供 Semantic Scholar 来源标识。
        source_records=[  # 保存两个来源的原始命中信息。
            PaperSourceRecord(source="openalex", external_id="W1", raw_rank=1, matched_subqueries=["Transformer forecasting ETT"]),  # 提供 OpenAlex 原始排名。
            PaperSourceRecord(source="semantic_scholar", external_id="S1", raw_rank=2),  # 提供 Semantic Scholar 原始排名。
        ],
        work_family_id="family-1",  # 关联后续版本族解析结果。
    )
    run_state = SearchRunState(  # 构造包含候选、成本和来源状态的运行状态。
        query_intent=query_intent,  # 固化当前运行的查询意图。
        search_mode="standard",  # 标记当前运行模式。
        max_rounds=2,  # 限制标准模式最多两轮搜索。
        current_round=1,  # 标记已完成第一轮搜索。
        selected_sources=["openalex", "semantic_scholar"],  # 保存固定双核心来源。
        normalized_papers=[paper],  # 保存统一论文候选。
        candidate_ids=["paper-1"],  # 保存进入排序阶段的候选标识。
        api_call_count=2,  # 记录两个来源调用次数。
        cache_hits=1,  # 记录一次缓存命中。
        status="running",  # 标记工作流仍在执行。
    )
    result = SearchResult(  # 构造与运行状态关联的最终响应摘要。
        papers=[paper],  # 返回多源融合论文记录。
        recalled_count=2,  # 记录原始召回数量。
        deduplicated_count=1,  # 记录去重后数量。
        run_id=run_state.run_id,  # 关联可恢复运行标识。
        source_counts={"openalex": 1, "semantic_scholar": 1},  # 返回按来源统计的数量。
        stop_reason="目标数量尚未满足，继续下一轮",  # 保存可解释的当前停止或继续说明。
    )
    assert result.run_id == run_state.run_id  # 验证 API 结果可关联搜索运行。
    assert result.papers[0].source_records[1].source == "semantic_scholar"  # 验证多源溯源信息被保留。
    assert run_state.candidate_ids == ["paper-1"]  # 验证运行状态保存排序候选标识。


def test_query_intent_rejects_conflicting_soft_preference_and_exclusion() -> None:
    """软偏好与排除条件包含相同词时应拒绝不可解释的计划。"""
    with pytest.raises(ValidationError, match="should_include 与 exclude"):  # 断言返回稳定的冲突错误。
        QueryIntent(  # 构造软偏好与排除词冲突的最小查询意图。
            original_query="检索时间序列论文",  # 提供合法原始查询。
            normalized_query="时间序列论文",  # 提供合法规范化查询。
            query_language="zh",  # 标记查询语言。
            should_include=["survey"],  # 提供冲突的软偏好词。
            exclude=["survey"],  # 提供相同的排除词。
        )


def test_search_run_state_rejects_excessive_round() -> None:
    """当前轮次超过最大轮次时应阻止工作流继续循环。"""
    with pytest.raises(ValidationError, match="current_round"):  # 断言轮次越界得到稳定错误。
        SearchRunState(  # 构造超过标准模式最大轮次的状态。
            query_intent=_build_query_intent(),  # 提供合法查询意图。
            search_mode="standard",  # 指定标准模式。
            max_rounds=2,  # 限制最多两轮。
            current_round=3,  # 模拟异常工作流进入第三轮。
        )
