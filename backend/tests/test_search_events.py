"""验证搜索进度事件的安全字段和多轮控制器发布顺序。"""

import asyncio  # 在同步测试中执行异步多轮控制器。

from backend.app.models.multi_source_recall import MultiSourceRecallResult  # 构造控制器所需的离线单轮结果。
from backend.app.models.candidate_generation import CandidateGenerationResult  # 构造不触发模型的排序前候选。
from backend.app.models.paper import PaperRecord  # 构造已核验的最终论文候选。
from backend.app.models.query_intent import QueryIntent  # 构造最小多轮搜索意图。
from backend.app.models.source_routing import SourceRoutePlan  # 构造可审计的离线来源计划。
from backend.app.services.multi_round_search import MultiRoundSearchController  # 导入待测的事件发布调用方。


class _StubCoordinator:
    """返回固定单轮结果且不访问来源、模型或网络。"""

    async def recall_candidates(self, query: QueryIntent) -> CandidateGenerationResult:
        """返回一篇排序前候选，促使控制器因无新查询而完成。"""
        paper = PaperRecord(paper_id="paper-1", title="Forecasting Paper", source="openalex", constraint_status="satisfied", llm_relevance_score=0.9)  # 构造可进入最终候选的论文。
        route_plan = SourceRoutePlan(academic_sources=["openalex"], selection_reasons={"openalex": "测试来源"})  # 构造唯一来源的可审计候选路由。
        return CandidateGenerationResult(route_plan=route_plan, query_intent=query, papers=[paper], academic_source_counts={"openalex": 1}, normalized_candidate_count=1, deduplicated_candidate_count=1, merged_candidate_count=0, filtered_candidate_count=0, work_family_count=0)  # 返回严格停在排序模型之前的最小候选边界。

    async def finalize_candidates(self, candidate_result: CandidateGenerationResult) -> MultiSourceRecallResult:
        """将唯一候选直接作为离线终态排序结果。"""
        return MultiSourceRecallResult(route_plan=candidate_result.route_plan, query_intent=candidate_result.query_intent, papers=candidate_result.papers, source_counts=candidate_result.source_counts, raw_paper_count=candidate_result.normalized_candidate_count, work_family_count=candidate_result.work_family_count)  # 测试事件发布时不加载模型或调用 LLM。


class _RecordingEventPublisher:
    """记录控制器发布事件而不创建真实 SSE 连接。"""

    def __init__(self) -> None:
        """初始化有序事件记录列表。"""
        self.events = []  # 保存按控制器调用顺序发布的事件。

    def publish(self, event: object) -> None:
        """记录事件对象供测试断言。"""
        self.events.append(event)  # 保持事件顺序用于验证控制流。


def test_controller_publishes_safe_lifecycle_events_without_query_text() -> None:
    """控制器应发布创建、节点、进度和完成事件，且事件不包含原始查询字段。"""
    publisher = _RecordingEventPublisher()  # 构造离线事件接收替身。
    query = QueryIntent(original_query="包含不应出现在事件中的私有研究问题", normalized_query="forecasting", query_language="zh", target_paper_count=2)  # 构造包含可用于泄漏检测的原始查询。

    result = asyncio.run(MultiRoundSearchController(_StubCoordinator()).run(query, event_publisher=publisher))  # 执行不访问外部服务的一轮控制器。

    event_types = [event.event_type for event in publisher.events]  # 提取发布顺序中的稳定事件类别。
    serialized_events = [event.model_dump_json() for event in publisher.events]  # 将事件编码为前端实际接收的 JSON 形式。
    assert event_types == ["run_created", "node_started", "node_completed", "completed"]  # 验证完整生命周期事件顺序。
    assert all(event.run_id == result.run_state.run_id for event in publisher.events)  # 验证所有事件关联同一次搜索运行。
    assert all("私有研究问题" not in payload for payload in serialized_events)  # 验证事件不会携带完整用户原始查询。
    assert publisher.events[-1].metrics == {"final_paper_count": 1}  # 验证完成事件仅包含轻量结果数量。
