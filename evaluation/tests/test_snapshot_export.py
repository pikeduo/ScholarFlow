"""测试受控候选快照导出器的映射、预检、写入和 CLI 授权边界。"""

import asyncio  # 在同步 pytest 用例中执行异步导出器。
from datetime import datetime, timezone  # 固定可复核的快照创建时间。
from pathlib import Path  # 构造 pytest 临时输入与输出路径。

import pytest  # 验证参数拒绝、文件保护和 CLI 退出行为。

from backend.app.models.candidate_generation import CandidateGenerationResult  # 构造排序前生产内部结果。
from backend.app.models.paper import PaperAuthor, PaperRecord, PaperSourceRecord  # 构造规范化、去重后的合成论文。
from backend.app.models.query_intent import QueryIntent  # 构造无需 Query Agent 的结构化查询。
from backend.app.models.source_routing import SourceRoutePlan  # 构造只包含学术来源的固定路由。
from evaluation.cli import main  # 验证显式在线授权命令边界。
from evaluation.contracts.snapshot import compute_snapshot_hash  # 复核导出内容哈希。
from evaluation.runners.snapshot_export import AllAcademicSourcesFailedError, export_candidate_snapshot, export_candidate_snapshot_to_file  # 执行纯替身候选导出并验证来源失败边界。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 验证写出的单条 JSONL 可被严格加载。


class FakeCandidateGenerator:
    """记录调用次数并返回固定候选结果的零网络测试替身。"""

    def __init__(self, result: CandidateGenerationResult) -> None:
        """保存固定结果并将调用计数初始化为零。"""
        self.result = result  # 保存不访问来源的合成结果。
        self.calls = 0  # 记录导出流程实际调用次数。

    async def generate(self, query: QueryIntent) -> CandidateGenerationResult:
        """返回固定结果，并确认执行查询没有在调用边界发生漂移。"""
        self.calls += 1  # 记录一次逻辑候选生成调用。
        assert query == self.result.query_intent  # 保证冻结意图就是服务实际消费的对象。
        return self.result  # 返回规则过滤后、模型排序前的结果。


def _query(**updates: object) -> QueryIntent:
    """构造显式区分来源召回和最终数量的单轮零模型查询。"""
    query = QueryIntent(original_query="图神经网络综述", normalized_query="graph neural network survey", query_language="zh", research_topics=["graph neural networks"], target_paper_count=10, source_recall_count=40, enable_semantic_ranking=False, enable_cross_encoder_ranking=False, requires_web_evidence=False)  # 构造安全默认意图。
    return query.model_copy(update=updates)  # 为拒绝路径覆盖单个字段且不引入无关变化。


def _result(query: QueryIntent | None = None) -> CandidateGenerationResult:
    """构造包含一次身份合并且 RRF 顺序故意打乱的候选结果。"""
    actual_query = query or _query()  # 默认使用可导出的单轮查询。
    second = PaperRecord(paper_id="p2", title="Second Paper", abstract="Second abstract", authors=[PaperAuthor(name="Bob")], year=2022, venue="Venue B", doi="10.1000/p2", source="openalex", openalex_id="W2", keywords=["graph"], source_records=[PaperSourceRecord(source="openalex", external_id="W2", raw_rank=2, matched_subqueries=["graph neural network survey"])], rrf_score=0.02)  # 先放置较低 RRF 候选以验证稳定重排。
    first = PaperRecord(paper_id="p1", title="First Paper", abstract="First abstract", authors=[PaperAuthor(name="Alice")], year=2023, venue="Venue A", doi="10.1000/p1", source="openalex", openalex_id="W1", open_access_url="https://example.test/p1", keywords=["survey"], source_records=[PaperSourceRecord(source="openalex", external_id="W1", raw_rank=1, matched_subqueries=["graph neural network survey"])], rrf_score=0.03)  # 构造较高 RRF 候选。
    return CandidateGenerationResult(route_plan=SourceRoutePlan(academic_sources=["openalex"], selection_reasons={"openalex": "合成主来源"}), query_intent=actual_query, papers=[second, first], academic_source_counts={"openalex": 3}, web_discovery_source_counts={}, cache_hit_count=1, normalized_candidate_count=3, deduplicated_candidate_count=2, merged_candidate_count=1, filtered_candidate_count=0, filter_reason_counts={}, work_family_count=0)  # 保持来源、融合、过滤数量严格守恒。


def _empty_result(
    query: QueryIntent | None = None,
    *,
    academic_sources: tuple[str, ...] = ("openalex",),
    academic_source_errors: dict[str, str] | None = None,
) -> CandidateGenerationResult:
    """构造来源成功为空或部分失败的零候选结果，不访问任何真实服务。

    参数：
        query：可选的冻结单轮 QueryIntent。
        academic_sources：本轮路由计划中的学术来源顺序。
        academic_source_errors：仅包含实际失败来源的安全错误摘要。
    返回：
        CandidateGenerationResult：满足生产阶段数量契约的零候选合成结果。
    """
    actual_query = query or _query()  # 默认使用可导出的单轮查询。
    source_errors = dict(academic_source_errors or {})  # 复制测试输入以避免调用后断言被外部修改。
    return CandidateGenerationResult(  # 构造能够区分成功空结果和来源失败的最小生产边界结果。
        route_plan=SourceRoutePlan(academic_sources=list(academic_sources), selection_reasons={source: "合成来源" for source in academic_sources}),  # 冻结全部计划学术来源。
        query_intent=actual_query,  # 保持替身实际消费的意图与导出输入一致。
        papers=[],  # 成功空结果或全失败结果都没有可排序论文。
        academic_source_counts={source: 0 for source in academic_sources},  # 所有来源均成功映射零篇或失败前未映射到论文。
        web_discovery_source_counts={},  # 快照导出固定不启用网页发现。
        academic_source_errors=source_errors,  # 仅记录测试指定的安全来源错误。
        normalized_candidate_count=0,  # 没有成功映射的统一论文记录。
        deduplicated_candidate_count=0,  # 无输入时身份融合后仍为空。
        merged_candidate_count=0,  # 无论文可合并。
        filtered_candidate_count=0,  # 无论文可进入规则过滤。
        filter_reason_counts={},  # 无过滤论文时原因统计必须为空。
        work_family_count=0,  # 无排序输入时不存在版本族。
    )


def test_export_maps_and_seals_pre_ranking_snapshot() -> None:
    """一次替身候选生成应映射为稳定排序、零模型用量的封存快照。"""
    generator = FakeCandidateGenerator(_result())  # 创建零网络候选服务。
    clock_values = iter([10.0, 10.125])  # 固定候选生成耗时为 125 毫秒。
    snapshot = asyncio.run(export_candidate_snapshot(generator, generator.result.query_intent, query_id="q-001", snapshot_id="snapshot-001", created_at=datetime(2026, 7, 19, tzinfo=timezone.utc), clock=lambda: next(clock_values)))  # 执行不写文件的映射闭环。
    assert generator.calls == 1  # 每份快照只能生成一次在线候选。
    assert [paper.paper_id for paper in snapshot.papers] == ["p1", "p2"]  # 候选按 RRF 降序稳定保存。
    assert [paper.snapshot_rank for paper in snapshot.papers] == [1, 2]  # 写入连续一基快照排名。
    assert snapshot.raw_candidate_count is None  # 不可观测的供应商原始条目数不得伪造。
    assert snapshot.normalized_candidate_count == 3  # 保存统一映射后的去重前数量。
    assert snapshot.deduplicated_candidate_count == 2  # 保存身份融合后的规则过滤前数量。
    assert snapshot.ranking_candidate_count == 2  # 保存实际离线排序输入数量。
    assert snapshot.source_recall_count == 40  # 来源召回上限保持独立字段。
    assert snapshot.target_paper_count == 10  # 最终目标数量不得替代来源召回上限。
    assert snapshot.query_intent["retrieval_round"] == 1  # 显式冻结生产序列化会排除的单轮字段。
    assert snapshot.usage.academic_api_calls == 1  # 保存逻辑学术来源调用数。
    assert snapshot.usage.actual_http_requests is None  # 不可观测的重试级 HTTP 数保持空值。
    assert snapshot.usage.llm_calls == 0 and snapshot.usage.total_tokens == 0  # 候选导出不调用 Query Agent 或 DeepSeek。
    assert snapshot.usage.latency_ms == pytest.approx(125.0)  # 只记录候选生成阶段耗时。
    assert snapshot.snapshot_hash == compute_snapshot_hash(snapshot)  # 声明哈希与规范化内容一致。


def test_export_writes_one_loadable_jsonl_and_refuses_existing_target(tmp_path: Path) -> None:
    """导出文件应可严格加载，且已有目标必须在候选调用前被拒绝。"""
    output_path = tmp_path / "snapshots" / "q-001.jsonl"  # 指定尚不存在的嵌套输出路径。
    generator = FakeCandidateGenerator(_result())  # 创建第一次成功导出的替身。
    snapshot = asyncio.run(export_candidate_snapshot_to_file(generator, generator.result.query_intent, query_id="q-001", snapshot_id="snapshot-001", output_path=output_path))  # 写出单条 UTF-8 JSONL。
    loaded = load_candidate_snapshots(output_path)  # 使用正式只读加载器复核文件。
    assert len(loaded) == 1 and loaded[0].snapshot_hash == snapshot.snapshot_hash  # 确认文件内容和返回快照一致。
    blocked_generator = FakeCandidateGenerator(_result())  # 创建用于验证预检顺序的新替身。
    with pytest.raises(FileExistsError, match="输出已存在"):  # 已有快照不得被覆盖。
        asyncio.run(export_candidate_snapshot_to_file(blocked_generator, blocked_generator.result.query_intent, query_id="q-001", snapshot_id="snapshot-002", output_path=output_path))
    assert blocked_generator.calls == 0  # 目标冲突必须发生在任何来源调用之前。


def test_export_rejects_all_failed_academic_sources_without_writing_snapshot(tmp_path: Path) -> None:
    """全部计划学术来源失败时必须拒绝封存零候选失败产物。"""
    output_path = tmp_path / "all-failed.snapshot.jsonl"  # 指定尚不存在的候选快照路径。
    generator = FakeCandidateGenerator(_empty_result(academic_source_errors={"openalex": "OpenAlex 请求参数无效（HTTP 400）"}))  # 构造唯一计划来源失败的零网络替身。
    with pytest.raises(AllAcademicSourcesFailedError, match="所有计划学术来源均失败"):  # 断言明确拒绝失败产物。
        asyncio.run(export_candidate_snapshot_to_file(generator, generator.result.query_intent, query_id="q-all-failed", snapshot_id="snapshot-all-failed", output_path=output_path))  # 执行仅命中替身的导出边界。
    assert generator.calls == 1  # 静态预检通过后应恰好观察到一次候选服务调用。
    assert not output_path.exists()  # 全部来源失败时不得留下可被评测读取的快照文件。


def test_export_allows_successful_empty_academic_result(tmp_path: Path) -> None:
    """学术 API 成功但确实返回零篇时仍应封存可评测的空快照。"""
    output_path = tmp_path / "successful-empty.snapshot.jsonl"  # 指定尚不存在的候选快照路径。
    generator = FakeCandidateGenerator(_empty_result())  # 构造来源成功且无结果的零网络替身。
    snapshot = asyncio.run(export_candidate_snapshot_to_file(generator, generator.result.query_intent, query_id="q-empty", snapshot_id="snapshot-empty", output_path=output_path))  # 执行并写出成功空结果快照。
    assert generator.calls == 1  # 成功空结果仍只调用一次候选服务。
    assert snapshot.ranking_candidate_count == 0 and snapshot.papers == []  # 明确保存可评测的真实空候选集合。
    assert output_path.exists() and load_candidate_snapshots(output_path)[0].snapshot_id == "snapshot-empty"  # 验证空快照仍符合正式加载器契约。


def test_export_allows_partial_academic_source_success_and_preserves_error(tmp_path: Path) -> None:
    """部分来源失败但仍有计划来源成功完成时应封存快照并保留安全错误。"""
    output_path = tmp_path / "partial-success.snapshot.jsonl"  # 指定尚不存在的候选快照路径。
    generator = FakeCandidateGenerator(_empty_result(academic_sources=("openalex", "semantic_scholar"), academic_source_errors={"openalex": "OpenAlex 请求参数无效（HTTP 400）"}))  # 构造 OpenAlex 失败而 Semantic Scholar 成功返回零篇的替身。
    snapshot = asyncio.run(export_candidate_snapshot_to_file(generator, generator.result.query_intent, query_id="q-partial", snapshot_id="snapshot-partial", output_path=output_path))  # 执行并封存部分成功结果。
    assert generator.calls == 1 and output_path.exists()  # 部分成功应保留一次候选调用和新快照文件。
    assert snapshot.warnings == ["学术来源降级 openalex: OpenAlex 请求参数无效（HTTP 400）"]  # 验证来源失败以安全摘要随快照保留。


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"source_recall_count": None}, "source_recall_count"),  # 拒绝将最终数量隐式当作来源召回规模。
        ({"retrieval_round": 2}, "retrieval_round=1"),  # 拒绝需要多轮控制器状态的查询。
        ({"requires_web_evidence": True}, "requires_web_evidence=false"),  # 拒绝网页发现进入学术候选快照。
        ({"enable_semantic_ranking": True}, "enable_semantic_ranking=false"),  # 拒绝在在线候选阶段声明执行 BGE-M3。
        ({"enable_cross_encoder_ranking": True}, "enable_cross_encoder_ranking=false"),  # 拒绝在在线候选阶段声明执行 Cross Encoder。
    ],
)
def test_export_rejects_unsafe_query_before_generator_call(updates: dict[str, object], message: str) -> None:
    """不安全或含糊的 QueryIntent 必须在候选生成前失败。"""
    query = _query(**updates)  # 构造单一违规字段。
    generator = FakeCandidateGenerator(_result(query))  # 创建可观察是否被调用的替身。
    with pytest.raises(ValueError, match=message):  # 验证错误明确指向违规配置。
        asyncio.run(export_candidate_snapshot(generator, query, query_id="q-unsafe", snapshot_id="snapshot-unsafe"))
    assert generator.calls == 0  # 所有静态边界都必须先于来源调用。


def test_cli_requires_explicit_online_authorization_before_loading_inputs(tmp_path: Path) -> None:
    """缺少在线授权开关时 CLI 不得读取 QueryIntent 或创建候选服务。"""
    factory_calls = 0  # 记录候选服务工厂是否被调用。

    def factory() -> FakeCandidateGenerator:
        """在被错误调用时记录并返回替身。"""
        nonlocal factory_calls  # 允许闭包更新调用计数。
        factory_calls += 1  # 记录一次不应发生的服务创建。
        return FakeCandidateGenerator(_result())  # 返回不会访问网络的替身。

    with pytest.raises(SystemExit) as exc_info:  # argparse 对缺失授权返回标准命令错误。
        main(["snapshot-export", "--query-intent", str(tmp_path / "missing.json"), "--query-id", "q-001", "--snapshot-id", "snapshot-001", "--output", str(tmp_path / "out.jsonl")], candidate_service_factory=factory)
    assert exc_info.value.code == 2  # 缺少授权属于命令使用错误。
    assert factory_calls == 0  # 未授权时不得装配生产或测试来源服务。


def test_cli_exports_with_injected_generator_without_network(tmp_path: Path) -> None:
    """显式授权的 CLI 可使用注入替身写出快照且不导入真实来源工厂。"""
    query = _query()  # 构造安全单轮 QueryIntent。
    query_path = tmp_path / "query-intent.json"  # 指定本地结构化查询输入。
    query_path.write_text(query.model_dump_json(), encoding="utf-8")  # 写入不含密钥的 UTF-8 测试输入。
    output_path = tmp_path / "snapshot.jsonl"  # 指定尚不存在的输出文件。
    generator = FakeCandidateGenerator(_result(query))  # 创建唯一候选服务实例。
    exit_code = main(["snapshot-export", "--query-intent", str(query_path), "--query-id", "q-001", "--snapshot-id", "snapshot-001", "--output", str(output_path), "--allow-online-sources"], candidate_service_factory=lambda: generator)  # 通过显式授权执行纯替身 CLI。
    assert exit_code == 0  # CLI 应成功返回。
    assert generator.calls == 1  # 整个命令只生成一次候选。
    assert load_candidate_snapshots(output_path)[0].query_id == "q-001"  # 写出结果可由正式快照加载器消费。


def test_cli_returns_nonzero_without_ok_when_all_academic_sources_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """全部计划学术来源失败时 CLI 必须返回非零且不得把失败产物写成成功快照。"""
    query = _query()  # 构造可通过静态导出预检的本地 QueryIntent。
    query_path = tmp_path / "query-intent.json"  # 指定纯本地结构化查询输入。
    query_path.write_text(query.model_dump_json(), encoding="utf-8")  # 写入无需网络和密钥的测试输入。
    output_path = tmp_path / "all-failed.snapshot.jsonl"  # 指定尚不存在的目标快照文件。
    generator = FakeCandidateGenerator(_empty_result(query, academic_source_errors={"openalex": "OpenAlex 请求参数无效（HTTP 400）"}))  # 构造唯一计划来源失败的零网络替身。
    exit_code = main(["snapshot-export", "--query-intent", str(query_path), "--query-id", "q-all-failed", "--snapshot-id", "snapshot-all-failed", "--output", str(output_path), "--allow-online-sources"], candidate_service_factory=lambda: generator)  # 通过显式授权执行完全离线替身 CLI。
    captured = capsys.readouterr()  # 获取 CLI 的标准输出以核验成功标记不会误报。
    assert exit_code == 1 and generator.calls == 1  # 验证失败边界向调用方返回稳定非零状态。
    assert "[ERROR]" in captured.out and "[OK]" not in captured.out  # 验证 CLI 只报告失败而不伪装为成功。
    assert not output_path.exists()  # 验证失败结果不会写成可被评测读取的 JSONL 文件。


def test_cli_rejects_existing_output_before_creating_generator(tmp_path: Path) -> None:
    """CLI 应在服务工厂执行前拒绝覆盖已有候选快照。"""
    query_path = tmp_path / "query-intent.json"  # 指定有效 QueryIntent 输入路径。
    query_path.write_text(_query().model_dump_json(), encoding="utf-8")  # 写入可通过静态预检的查询。
    output_path = tmp_path / "existing.jsonl"  # 指定将被预先占用的输出路径。
    output_path.write_text("preserve\n", encoding="utf-8")  # 模拟用户已有且必须保留的文件。
    factory_calls = 0  # 记录候选服务是否被错误装配。

    def factory() -> FakeCandidateGenerator:
        """记录服务创建并返回零网络替身。"""
        nonlocal factory_calls  # 允许闭包更新调用计数。
        factory_calls += 1  # 记录一次不应发生的服务创建。
        return FakeCandidateGenerator(_result())  # 返回合成候选生成器。

    with pytest.raises(FileExistsError, match="输出已存在"):  # 已有目标必须作为稳定文件错误返回。
        main(["snapshot-export", "--query-intent", str(query_path), "--query-id", "q-001", "--snapshot-id", "snapshot-001", "--output", str(output_path), "--allow-online-sources"], candidate_service_factory=factory)
    assert factory_calls == 0  # 输出预检必须先于生产适配器装配。
    assert output_path.read_text(encoding="utf-8") == "preserve\n"  # 已有内容不得被改变。
