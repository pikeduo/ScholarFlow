"""测试 Cross Encoder 离线适配器的本地路径与延迟加载边界。"""

from pathlib import Path  # 构造不含真实权重的临时本地模型目录。

import pytest  # 断言路径和查询输入边界。

from evaluation.adapters.cross_encoder import CrossEncoderOfflineScorer, CrossEncoderOfflineScorerError  # 测试独立评测适配器。
from evaluation.contracts.snapshot import CandidatePaper  # 构造最小封存候选输入。


class _StubReranker:
    """提供不加载模型的 FlagReranker 替身。"""

    def __init__(self) -> None:
        """初始化对输入对和批大小的内存记录。"""
        self.calls: list[tuple[list[list[str]], int]] = []  # 保存本次评分的公开输入审计。

    def compute_score(self, pairs: list[list[str]], *, batch_size: int) -> list[float]:
        """返回与输入等长的稳定合成分数。"""
        self.calls.append((pairs, batch_size))  # 记录适配器没有改变候选顺序。
        return [float(index) for index, _pair in enumerate(pairs)]  # 返回一一对应的可预测分数。


def _paper() -> CandidatePaper:
    """构造可供 Cross Encoder 使用的最小排序前候选。"""
    return CandidatePaper(paper_id="paper-1", title="A Paper", source="fixture", abstract="An abstract", rrf_score=1.0, snapshot_rank=1)  # 满足快照候选契约。


def test_scores_only_after_explicit_nonempty_call(tmp_path: Path) -> None:
    """构造和空候选评分不加载模型，非空评分只使用用户目录。"""
    model_dir = tmp_path / "reranker"  # 模拟用户已下载的模型目录。
    model_dir.mkdir()  # 创建本地目录。
    (model_dir / "config.json").write_text("{}", encoding="utf-8")  # 提供最小完整性标记。
    reranker = _StubReranker()  # 注入零模型替身。
    factory_calls: list[tuple[Path, str]] = []  # 记录延迟工厂调用。

    def factory(path: Path, device: str) -> _StubReranker:
        """记录只传入验证后的本地路径与显式设备。"""
        factory_calls.append((path, device))  # 审计延迟加载发生时机。
        return reranker  # 返回固定替身。

    scorer = CrossEncoderOfflineScorer(model_dir, batch_size=3, reranker_factory=factory)  # 构造期不得加载模型。
    assert factory_calls == []  # 验证构造期没有调用工厂。
    assert scorer.score("query", {}, []).scores == []  # 空候选直接返回空分数。
    assert factory_calls == []  # 验证空候选仍不加载模型。
    result = scorer.score(" query\ntext ", {}, [_paper()])  # 非空候选首次触发本地评分。
    assert result.scores == [0.0]  # 验证分数与候选严格对齐。
    assert factory_calls == [(model_dir, "cpu")]  # 验证只使用本地目录。
    assert reranker.calls[0][0] == [["query text", "A Paper\nAn abstract"]]  # 验证查询与论文文本稳定构造。
    assert reranker.calls[0][1] == 3  # 验证批大小透传。


def test_rejects_incomplete_local_directory(tmp_path: Path) -> None:
    """缺少 config.json 的目录不得进入模型库或触发下载。"""
    model_dir = tmp_path / "missing-config"  # 创建不完整目录。
    model_dir.mkdir()  # 不写 config.json。
    with pytest.raises(CrossEncoderOfflineScorerError, match="包含 config.json"):  # 必须在构造期拒绝。
        CrossEncoderOfflineScorer(model_dir)  # 不访问网络。
