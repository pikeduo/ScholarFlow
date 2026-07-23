"""提供只读取用户本地 Cross Encoder 目录的评测离线打分适配器。"""

from collections.abc import Callable, Sequence  # 声明可替换本地重排器和候选序列。
from pathlib import Path  # 限制模型必须为用户已准备的本地目录。
from re import sub  # 规范化公开文本的连续空白。
from time import perf_counter  # 记录本地重排耗时。

from evaluation.contracts.ablation import RankingScoreBatch  # 返回统一评分与运行统计。
from evaluation.contracts.snapshot import CandidatePaper  # 读取封存候选论文。


class CrossEncoderOfflineScorerError(RuntimeError):
    """表示本地 Cross Encoder 路径、依赖或推理不可用。"""


class CrossEncoderOfflineScorer:
    """将本地 bge-reranker-v2-m3 适配为评测 `OfflineRankingScorer`。"""

    def __init__(self, model_path: Path, *, model_name: str = "BAAI/bge-reranker-v2-m3", device: str = "cpu", batch_size: int = 8, reranker_factory: Callable[[Path, str], object] | None = None) -> None:
        """校验本地目录但不导入、下载或加载模型。"""
        self._model_path = Path(model_path)  # 规范化用户明确提供的模型目录。
        if not self._model_path.is_dir() or not (self._model_path / "config.json").is_file():  # 禁止远程名称或不完整目录进入模型库。
            raise CrossEncoderOfflineScorerError("Cross Encoder 评测模型目录必须存在且包含 config.json")  # 防止运行时联网下载。
        if device not in {"cpu", "cuda"} or batch_size < 1:  # 设备和批大小必须可审计。
            raise ValueError("Cross Encoder 的 device 必须为 cpu 或 cuda，batch_size 必须大于零")  # 及早拒绝无效配置。
        self._model_name, self._device, self._batch_size = model_name, device, batch_size  # 保存运行元数据。
        self._factory = reranker_factory or _create_flag_reranker  # 保留测试替身边界。
        self._reranker: object | None = None  # 延迟到首次非空评分才加载模型。

    def score(self, query: str, query_intent: object, papers: Sequence[CandidatePaper]) -> RankingScoreBatch:
        """按候选顺序计算查询—论文对分数，不读取外部状态。"""
        del query_intent  # 不推测未冻结的结构化条件。
        normalized_query = _normalize(query)  # 统一无语义空白。
        if not normalized_query:
            raise ValueError("Cross Encoder 评测查询不能为空")  # 空查询不能形成解释性分数。
        if not papers:
            return RankingScoreBatch(scores=[], model_name=self._model_name, latency_ms=0.0, device=self._device, batch_size=self._batch_size)  # 空候选不加载模型。
        pairs = [[normalized_query, _paper_text(paper)] for paper in papers]  # 保持与输入候选严格一一对应。
        started = perf_counter()  # 仅统计实际本地推理阶段。
        reranker = self._get_reranker()  # 首次非空评分时才加载本地模型。
        try:
            scores = reranker.compute_score(pairs, batch_size=self._batch_size)  # 调用 FlagEmbedding 的成对重排接口。
        except Exception as error:
            raise CrossEncoderOfflineScorerError("Cross Encoder 本地评分不可用") from error  # 不泄露路径或底层错误。
        scores = [float(scores)] if isinstance(scores, (int, float)) else [float(score) for score in scores]  # 兼容单对与多对返回形状。
        if len(scores) != len(papers):
            raise CrossEncoderOfflineScorerError("Cross Encoder 评分数量与候选数量不一致")  # 拒绝错位排序。
        return RankingScoreBatch(scores=scores, model_name=self._model_name, latency_ms=(perf_counter() - started) * 1000, device=self._device, batch_size=self._batch_size)  # 返回可审计分数。

    def _get_reranker(self) -> object:
        """延迟创建本地重排器，避免构造或空候选加载模型。"""
        if self._reranker is None:
            self._reranker = self._factory(self._model_path, self._device)  # 工厂只接收已验证本地目录。
        return self._reranker  # 复用同一进程实例。


def _create_flag_reranker(model_path: Path, device: str) -> object:
    """延迟导入 FlagEmbedding 并仅传入本地模型路径。"""
    from FlagEmbedding import FlagReranker  # 用户显式执行评分时才导入可选依赖。

    return FlagReranker(str(model_path), use_fp16=device == "cuda", device=device)  # 不接受远程仓库名。


def _paper_text(paper: CandidatePaper) -> str:
    """构造 Cross Encoder 使用的标题与摘要文本。"""
    title = _normalize(paper.title)  # 标题始终保留。
    if not title:
        raise CrossEncoderOfflineScorerError("Cross Encoder 候选论文标题不能为空")  # 拒绝不可解释输入。
    abstract = _normalize(paper.abstract)  # 仅使用快照公开摘要。
    return f"{title}\n{abstract}" if abstract else title  # 缺摘要时不添加空行。


def _normalize(value: str) -> str:
    """压缩连续空白但不改写语义。"""
    return sub(r"\s+", " ", value).strip()  # 统一换行和制表符。
