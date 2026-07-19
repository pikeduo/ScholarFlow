"""测试评测 BGE-M3 适配器的纯本地输入边界与替身评分行为。"""

from collections.abc import Sequence  # 标注替身编码器接收的论文文本序列。
from pathlib import Path  # 构造临时本地模型目录。

import pytest  # 断言本地路径和模型输出异常。

from evaluation.adapters.bge_m3 import BGE_M3_EVALUATION_TEXT_VERSION, BgeM3OfflineOutOfMemoryError, BgeM3OfflineScorer, BgeM3OfflineScorerError  # 导入待测本地评分器与稳定错误。
from evaluation.contracts.snapshot import CandidatePaper  # 构造不依赖真实快照文件的候选论文。


class _RecordingEncoder:
    """记录本地文本和批大小的纯替身，不导入或加载模型。"""

    def __init__(self, scores: list[float], *, oom_once: bool = False) -> None:
        """保存预设分数及可选的一次 OOM 行为。"""
        self._scores = scores  # 保存与候选顺序一致的测试分数。
        self._oom_once = oom_once  # 控制首轮是否模拟可恢复内存不足。
        self.calls: list[tuple[str, list[str], int]] = []  # 审计查询、论文文本和实际批大小。

    def score(self, query_text: str, document_texts: Sequence[str], *, batch_size: int) -> list[float]:
        """记录调用；可在首轮抛出已净化 OOM 异常。"""
        self.calls.append((query_text, list(document_texts), batch_size))  # 保存无需外部资源的调用证据。
        if self._oom_once:  # 仅模拟一次批大小恢复路径。
            self._oom_once = False  # 避免第二次调用继续失败。
            raise BgeM3OfflineOutOfMemoryError("测试 OOM")  # 触发评分器的单次降批策略。
        return list(self._scores)  # 返回固定分数，不执行模型推理。


def _local_model_directory(tmp_path: Path) -> Path:
    """构造带最小配置文件的假本地模型目录，不包含真实权重。"""
    model_path = tmp_path / "bge-m3-local"  # 使用测试临时目录模拟用户显式本地模型位置。
    model_path.mkdir()  # 创建仅供路径预检使用的目录。
    (model_path / "config.json").write_text("{}\n", encoding="utf-8")  # 满足离线模型目录的最小安全边界。
    return model_path  # 返回不会触发网络下载的本地路径。


def _paper(paper_id: str, *, title: str, keywords: list[str] | None = None, abstract: str = "", venue: str | None = None, year: int | None = None) -> CandidatePaper:
    """构造最小有效候选论文，字段只来自评测快照契约。"""
    return CandidatePaper(paper_id=paper_id, title=title, keywords=keywords or [], abstract=abstract, venue=venue, year=year, source="openalex", rrf_score=1.0, snapshot_rank=1)  # 提供可用于版本化文本构造的固定候选。


def test_scores_local_candidates_with_versioned_public_text_and_no_model_download(tmp_path: Path) -> None:
    """评分器应通过替身使用稳定公开文本，且只记录本地模型审计信息。"""
    encoder = _RecordingEncoder([0.25, 0.75])  # 注入不访问网络或模型的确定性替身。
    scorer = BgeM3OfflineScorer(_local_model_directory(tmp_path), batch_size=3, encoder_factory=lambda _path, _device, _fp16: encoder)  # 构造时不创建替身或加载模型。
    result = scorer.score("  graph\n neural networks  ", {}, [_paper("paper-a", title=" First\tPaper ", keywords=[" graph ", "", "neural   network"], abstract=" Abstract\ntext ", venue=" Venue ", year=2024), _paper("paper-b", title="Second Paper")])  # 对两条冻结候选执行纯替身评分。

    assert result.scores == [0.25, 0.75]  # 验证分数顺序与候选输入一致。
    assert result.model_name == "BAAI/bge-m3;text=evaluation_bge_m3_text_v1"  # 验证审计标识不是模型目录或远程下载请求，并冻结文本格式版本。
    assert result.device == "cpu"  # 验证默认设备明确写入统计。
    assert result.batch_size == 3  # 验证首轮批大小被冻结。
    assert result.oom_retry_count == 0  # 验证正常路径未发生降批。
    assert encoder.calls == [("graph neural networks", ["Title: First Paper\nKeywords: graph, neural network\nAbstract: Abstract text\nVenue: Venue\nYear: 2024", "Title: Second Paper\nKeywords: \nAbstract: \nVenue: Unknown\nYear: Unknown"], 3)]  # 验证版本化字段顺序和空值占位符。
    assert BGE_M3_EVALUATION_TEXT_VERSION == "evaluation_bge_m3_text_v1"  # 冻结文本格式版本常量。


def test_empty_candidates_do_not_create_encoder_or_load_model(tmp_path: Path) -> None:
    """空候选快照应返回空审计结果，绝不触发编码器工厂。"""
    factory_calls = 0  # 记录工厂是否被调用。

    def factory(_path: Path, _device: str, _fp16: bool) -> _RecordingEncoder:
        """若空候选错误加载模型，立即让测试失败。"""
        nonlocal factory_calls  # 在嵌套工厂中修改断言计数。
        factory_calls += 1  # 记录不应发生的模型创建。
        return _RecordingEncoder([])  # 返回不会实际使用的替身。

    result = BgeM3OfflineScorer(_local_model_directory(tmp_path), encoder_factory=factory).score("query", {}, [])  # 执行空候选评分。

    assert result.scores == []  # 验证返回与输入等长的空分数。
    assert result.latency_ms == 0.0  # 验证模型未执行而不是伪造耗时。
    assert factory_calls == 0  # 验证没有创建、加载或下载模型。


def test_oom_retries_once_with_halved_batch_size(tmp_path: Path) -> None:
    """首轮 OOM 只应以减半批大小重试一次，并记录审计统计。"""
    encoder = _RecordingEncoder([0.5], oom_once=True)  # 注入首轮失败、第二轮成功的本地替身。
    scorer = BgeM3OfflineScorer(_local_model_directory(tmp_path), batch_size=5, encoder_factory=lambda _path, _device, _fp16: encoder)  # 配置可减半的初始批大小。
    result = scorer.score("query", {}, [_paper("paper-a", title="Paper")])  # 执行单候选评分。

    assert [call[2] for call in encoder.calls] == [5, 2]  # 验证仅一次向下取整减半重试。
    assert result.batch_size == 2  # 验证记录成功轮次的实际批大小。
    assert result.oom_retry_count == 1  # 验证审计记录一次可恢复内存不足。


def test_rejects_nonlocal_or_incomplete_model_directory(tmp_path: Path) -> None:
    """远程模型名、缺失目录和没有配置的目录都必须在构造期被拒绝。"""
    with pytest.raises(BgeM3OfflineScorerError, match="已存在的本地目录"):  # 不允许把仓库名误当本地路径。
        BgeM3OfflineScorer(Path("BAAI/bge-m3"))  # 该相对路径不存在时不能交给模型库解析。
    incomplete_path = tmp_path / "incomplete"  # 构造没有模型配置的本地目录。
    incomplete_path.mkdir()  # 仅创建目录，不伪造可运行模型。
    with pytest.raises(BgeM3OfflineScorerError, match="缺少 config.json"):  # 配置缺失时不得运行时联网补全。
        BgeM3OfflineScorer(incomplete_path)  # 验证本地完整性边界。
    with pytest.raises(ValueError, match="device 必须为 cpu 或 cuda"):  # 设备必须明确，避免隐式选择。
        BgeM3OfflineScorer(_local_model_directory(tmp_path), device="auto")  # 评测结果不能记录模糊设备。
