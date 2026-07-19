"""测试计划内离线排序执行的模型授权边界与原子归档。"""

import json  # 读取测试生成的 manifest 与结果 JSONL。
from collections.abc import Mapping, Sequence  # 标注纯替身评分器输入。
from pathlib import Path  # 定位内置合成输入和临时输出。

import pytest  # 断言未实现阶段与输出保护边界。

from evaluation.contracts.ablation import RankingScoreBatch, build_standard_ablation_matrix  # 构造与 fixture 一致的计划矩阵。
from evaluation.contracts.snapshot import CandidatePaper  # 标注替身候选输入。
from evaluation.runners.offline_execution import execute_ablation_to_files  # 导入待测执行归档入口。
from evaluation.runners.offline_ranking import build_ablation_plan, write_ablation_plan  # 生成正式计划输入而不执行模型。
from evaluation.runners.snapshot_loader import load_candidate_snapshots  # 读取纯合成封存快照。


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]  # 稳定定位仓库根目录。
SNAPSHOTS_PATH = REPOSITORY_ROOT / "evaluation" / "fixtures" / "candidate_snapshots.jsonl"  # 使用不代表真实成绩的合成集合。


class _StubBgeScorer:
    """记录调用并返回固定分数的零模型离线替身。"""

    def __init__(self) -> None:
        """初始化调用次数，证明只有 B 实验使用评分器。"""
        self.calls = 0  # 保存纯内存调用计数。

    def score(self, _query: str, _query_intent: Mapping[str, object], papers: Sequence[CandidatePaper]) -> RankingScoreBatch:
        """按候选顺序返回递增分数，不加载模型或访问网络。"""
        self.calls += 1  # 记录 B 阶段实际评分一次。
        return RankingScoreBatch(scores=[float(index) for index, _paper in enumerate(papers)], model_name="stub-bge", latency_ms=1.0, device="cpu", batch_size=2)  # 返回稳定且等长的合成分数。


class _StubCrossEncoderScorer:
    """记录调用并返回反向分数的零模型 Cross Encoder 替身。"""

    def __init__(self) -> None:
        """初始化内存调用计数，不加载模型或读取网络。"""
        self.calls = 0  # 保存测试期实际评分次数。

    def score(self, _query: str, _query_intent: Mapping[str, object], papers: Sequence[CandidatePaper]) -> RankingScoreBatch:
        """按输入候选返回稳定分数，验证 C/D 的显式注入边界。"""
        self.calls += 1  # 记录每个启用 Cross Encoder 的实验调用。
        return RankingScoreBatch(scores=[float(len(papers) - index) for index, _paper in enumerate(papers)], model_name="stub-cross", latency_ms=1.0, device="cpu", batch_size=2)  # 返回严格等长的合成分数。


def _matrix_and_plan(tmp_path: Path) -> tuple[Path, Path]:
    """写入与合成快照匹配的矩阵和计划，不触发任何模型。"""
    matrix = build_standard_ablation_matrix(semantic_top_k=4, cross_encoder_top_k=2, target_paper_count=2)  # 构造可执行的标准四组矩阵。
    matrix_path = tmp_path / "matrix.json"  # 使用用户可替换的本地矩阵路径。
    matrix_path.write_text(json.dumps(matrix.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")  # 写入 UTF-8 测试矩阵。
    plan_path = tmp_path / "plan.json"  # 为执行入口准备已审核计划。
    write_ablation_plan(build_ablation_plan(load_candidate_snapshots(SNAPSHOTS_PATH), matrix), plan_path)  # 只生成零 API、零 DeepSeek 计划。
    return matrix_path, plan_path  # 返回匹配同一快照集合的输入路径。


def test_executes_ab_plan_subset_and_archives_atomic_results(tmp_path: Path) -> None:
    """A/B 应复用同一封存快照并写出结果和完整审计 manifest。"""
    matrix_path, plan_path = _matrix_and_plan(tmp_path)  # 准备已验证的计划和矩阵。
    scorer = _StubBgeScorer()  # 注入不加载模型的 BGE 替身。
    output_path = tmp_path / "results.jsonl"  # 选择尚不存在的结果输出。
    manifest_path = tmp_path / "results.manifest.json"  # 选择尚不存在的审计 manifest。

    manifest = execute_ablation_to_files(run_id="fixture-ab", snapshots_path=SNAPSHOTS_PATH, matrix_path=matrix_path, plan_path=plan_path, experiment_ids=["A", "B"], output_path=output_path, manifest_path=manifest_path, semantic_scorer=scorer)  # 执行计划内 A/B 子集。

    assert scorer.calls == 1  # 验证仅 B 实验使用本地评分替身。
    assert manifest.task_count == 2  # 一份快照乘两个选择实验。
    assert manifest.local_model_stages == ["bge_m3"]  # 验证归档只记录实际执行阶段。
    assert len(output_path.read_text(encoding="utf-8").strip().splitlines()) == 2  # 验证一行归档一个离线实验结果。
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))  # 读取已原子发布的 manifest。
    assert persisted["selected_experiment_ids"] == ["A", "B"]  # 验证保持矩阵稳定实验顺序。
    with pytest.raises(FileExistsError, match="结果已存在"):  # 已归档结果不得被同名执行覆盖。
        execute_ablation_to_files(run_id="fixture-ab", snapshots_path=SNAPSHOTS_PATH, matrix_path=matrix_path, plan_path=plan_path, experiment_ids=["A"], output_path=output_path, manifest_path=tmp_path / "another.manifest.json")  # 验证输出保护先于模型执行。


def test_executes_cross_encoder_experiments_only_with_explicit_scorer(tmp_path: Path) -> None:
    """C/D 必须复用同一快照，并要求调用方显式注入 Cross Encoder 评分器。"""
    matrix_path, plan_path = _matrix_and_plan(tmp_path)  # 准备正常输入以隔离阶段拒绝原因。
    output_path = tmp_path / "cross.jsonl"  # 选择理论输出位置。
    manifest_path = tmp_path / "cross.manifest.json"  # 选择理论 manifest 位置。

    with pytest.raises(ValueError, match="必须显式提供 cross_encoder_scorer"):  # 缺少显式授权评分器时必须在写输出前失败。
        execute_ablation_to_files(run_id="fixture-cross", snapshots_path=SNAPSHOTS_PATH, matrix_path=matrix_path, plan_path=plan_path, experiment_ids=["C"], output_path=output_path, manifest_path=manifest_path)  # 不注入本地精排器。
    assert not output_path.exists()  # 验证失败不写半截结果。
    assert not manifest_path.exists()  # 验证失败不写误导性审计记录。
    scorer = _StubCrossEncoderScorer()  # 注入不加载真实模型的替身。
    manifest = execute_ablation_to_files(run_id="fixture-cross", snapshots_path=SNAPSHOTS_PATH, matrix_path=matrix_path, plan_path=plan_path, experiment_ids=["C", "D"], output_path=output_path, manifest_path=manifest_path, semantic_scorer=_StubBgeScorer(), cross_encoder_scorer=scorer)  # C/D 复用同一快照并分别执行需要的阶段。
    assert manifest.selected_experiment_ids == ["C", "D"]  # 验证矩阵稳定顺序。
    assert manifest.local_model_stages == ["bge_m3", "cross_encoder"]  # 验证 D 的 BGE 与 C/D 的 Cross 阶段均被冻结。
    assert scorer.calls == 2  # C 与 D 各执行一次 Cross Encoder 评分。
