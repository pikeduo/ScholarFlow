"""测试共享候选快照上的 A/B/C/D 完全离线排序消融。"""

from collections.abc import Mapping, Sequence  # 标注测试打分器输入。
from pathlib import Path  # 定位内置合成快照和矩阵配置。

import pytest  # 验证缺失适配器和 DeepSeek 边界。

from evaluation.contracts.ablation import AblationExperiment, AblationMatrix, RankingScoreBatch, build_standard_ablation_matrix  # 构造标准和非法矩阵。
from evaluation.contracts.prediction import RankingConfig  # 构造单个排序配置。
from evaluation.contracts.snapshot import CandidatePaper, compute_snapshot_hash  # 验证共享快照不变。
from evaluation.runners.offline_ranking import build_ablation_plan, load_ablation_matrix, run_ablation_matrix, run_offline_experiment  # 执行计划和替身排序。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 读取已封存合成快照。


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]  # 稳定定位仓库根目录。
SNAPSHOT_FIXTURE = REPOSITORY_ROOT / "evaluation" / "fixtures" / "candidate_snapshots.jsonl"  # 定位共享候选快照。
MATRIX_CONFIG = REPOSITORY_ROOT / "evaluation" / "config" / "ablation_default.json"  # 定位默认 A/B/C/D 配置。


class _RecordingScorer:
    """按 paper_id 返回固定分数并记录每次候选输入的纯离线替身。"""

    def __init__(self, name: str, scores: dict[str, float]) -> None:
        """保存替身名称和论文分数映射。"""
        self._name = name  # 保存报告中的模型替身名称。
        self._scores = scores  # 保存无需模型的固定分数。
        self.calls: list[list[str]] = []  # 记录每次输入论文顺序。

    def score(self, query: str, query_intent: Mapping[str, object], papers: Sequence[CandidatePaper]) -> RankingScoreBatch:
        """记录输入、尝试修改副本并返回等长固定分数。"""
        assert query == "graph neural networks for scholarly recommendation"  # 验证使用冻结查询。
        self.calls.append([paper.paper_id for paper in papers])  # 保存本次阶段输入。
        if papers:  # 证明打分器只能接触隔离副本。
            papers[0].title = "mutated scorer copy"  # 修改副本不应污染运行器或共享快照。
        if isinstance(query_intent, dict):  # 测试运行器传入独立 QueryIntent 副本。
            query_intent["mutated"] = True  # 修改副本不应改变快照哈希。
        return RankingScoreBatch(scores=[self._scores[paper_id] for paper_id in self.calls[-1]], model_name=self._name, latency_ms=10.0, device="cpu", batch_size=2)  # 返回稳定本地统计。


def test_default_matrix_has_abcd_and_zero_deepseek() -> None:
    """默认 JSON 与工厂都应生成共享口径的 A/B/C/D 第一轮矩阵。"""
    loaded = load_ablation_matrix(MATRIX_CONFIG)  # 校验可提交默认矩阵。
    generated = build_standard_ablation_matrix()  # 校验代码工厂。
    assert [experiment.experiment_id for experiment in loaded.experiments] == ["A", "B", "C", "D"]  # JSON 按标准顺序保存。
    assert [experiment.experiment_id for experiment in generated.experiments] == ["A", "B", "C", "D"]  # 工厂按相同顺序生成。
    assert all(not experiment.ranking_config.deepseek_enabled for experiment in loaded.experiments)  # 第一轮不允许 DeepSeek。
    assert {tuple(experiment.ranking_config.evaluation_top_k) for experiment in loaded.experiments} == {(5, 10, 20)}  # 全矩阵共享评分口径。


def test_abcd_reuses_snapshot_and_routes_expected_candidate_sets() -> None:
    """四组配置应从同一快照开始，并按阶段开关传递正确候选集合。"""
    snapshot = load_candidate_snapshots(SNAPSHOT_FIXTURE)[0]  # 读取唯一已封存快照。
    assert snapshot.snapshot_stage == "pre_semantic_ranking"  # 确认四组实验共享规则过滤后、BGE-M3 前的候选集合。
    assert snapshot.ranking_candidate_count == len(snapshot.papers) == 4  # 明确离线排序输入不等同于过滤前去重数量。
    original_hash = compute_snapshot_hash(snapshot)  # 保存运行前内容摘要。
    matrix = build_standard_ablation_matrix(semantic_top_k=2, cross_encoder_top_k=2, target_paper_count=2)  # 使用小规模截断便于断言。
    semantic = _RecordingScorer("stub-bge", {"candidate-a": 0.7, "candidate-b": 0.2, "candidate-c": 0.9, "candidate-d": 0.1})  # 令 BGE 顺序为 C、A、B、D。
    cross = _RecordingScorer("stub-cross", {"candidate-a": 0.8, "candidate-b": 0.95, "candidate-c": 0.6, "candidate-d": 0.1})  # 令完整 CE 顺序为 B、A、C、D。
    results = run_ablation_matrix([snapshot], matrix, semantic_scorer=semantic, cross_encoder_scorer=cross)  # 使用纯替身执行四组配置。
    predictions = {result.experiment_id: [paper.paper_id for paper in result.prediction.papers] for result in results}  # 汇总最终论文顺序。
    assert predictions == {"A": ["candidate-a", "candidate-b"], "B": ["candidate-c", "candidate-a"], "C": ["candidate-b", "candidate-a"], "D": ["candidate-a", "candidate-c"]}  # 验证四条路径。
    full_order = ["candidate-a", "candidate-b", "candidate-c", "candidate-d"]  # 保存快照 RRF 顺序。
    assert semantic.calls == [full_order, full_order]  # B 和 D 都从完整同一快照执行 BGE。
    assert cross.calls == [full_order, ["candidate-c", "candidate-a"]]  # C 读取完整快照，D 读取 BGE top-2。
    assert all(result.snapshot_hash == original_hash for result in results)  # 每个结果冻结相同快照哈希。
    assert compute_snapshot_hash(snapshot) == original_hash  # 打分器修改隔离副本后共享快照仍未变化。
    assert snapshot.papers[0].title == "Graph Neural Networks for Scholarly Recommendation"  # 原始候选文本未被替身污染。


def test_plan_is_zero_api_zero_deepseek_and_checks_source_recall_count() -> None:
    """计划应显式记录零外部调用，并拒绝召回规模不同的快照复用。"""
    snapshot = load_candidate_snapshots(SNAPSHOT_FIXTURE)[0]  # 读取 source_recall_count=50 的快照。
    matrix = build_standard_ablation_matrix()  # 构造匹配矩阵。
    plan = build_ablation_plan([snapshot], matrix)  # 仅生成计划，不运行打分器。
    assert plan.task_count == 4  # 一份快照乘四组配置。
    assert plan.academic_api_calls == 0  # 计划不会调用学术 API。
    assert plan.deepseek_calls == 0  # 第一轮不会调用 DeepSeek。
    mismatched = build_standard_ablation_matrix(source_recall_count=20, semantic_top_k=20, cross_encoder_top_k=10, target_paper_count=10)  # 构造不同在线召回规模。
    with pytest.raises(ValueError, match="source_recall_count 与消融矩阵不一致"):  # 验证必须重新生成快照。
        build_ablation_plan([snapshot], mismatched)


def test_enabled_model_requires_explicit_scorer_and_deepseek_is_forbidden() -> None:
    """运行器不得自动加载本地模型，矩阵不得启用 DeepSeek。"""
    snapshot = load_candidate_snapshots(SNAPSHOT_FIXTURE)[0]  # 读取合成快照。
    experiment = build_standard_ablation_matrix().experiments[1]  # 选择启用 BGE-M3 的 B 配置。
    with pytest.raises(ValueError, match="未提供离线 semantic_scorer"):  # 验证不存在隐式模型实例化。
        run_offline_experiment(snapshot, "matrix", experiment)
    with pytest.raises(ValueError, match="必须关闭 DeepSeek"):  # 验证第一轮 Token 边界。
        AblationExperiment(experiment_id="E", label="invalid", ranking_config=RankingConfig(deepseek_enabled=True))


def test_matrix_rejects_mixed_evaluation_top_k() -> None:
    """同一横向比较不得混用不同评分截断。"""
    first = AblationExperiment(experiment_id="A", label="one", ranking_config=RankingConfig(evaluation_top_k=[5, 10]))  # 构造第一套评分口径。
    second = AblationExperiment(experiment_id="B", label="two", ranking_config=RankingConfig(evaluation_top_k=[5, 20]))  # 构造冲突评分口径。
    with pytest.raises(ValueError, match="必须共享 evaluation_top_k"):  # 验证矩阵层拒绝混用。
        AblationMatrix(matrix_id="mixed", experiments=[first, second])
