"""提供只读取用户本地 BGE-M3 目录的评测离线打分适配器。"""

from collections.abc import Callable, Sequence  # 声明可替换编码器工厂与候选序列类型。
from pathlib import Path  # 约束模型必须来自用户指定的本地目录。
from re import sub  # 规范化公开元数据中的连续空白。
from time import perf_counter  # 记录本地模型实际评分耗时。
from typing import Protocol  # 隔离 FlagEmbedding 的可替换最小接口。

from evaluation.contracts.ablation import RankingScoreBatch  # 返回离线排序运行器所需的统一分数契约。
from evaluation.contracts.snapshot import CandidatePaper  # 读取已封存候选论文的公开字段。


BGE_M3_EVALUATION_TEXT_VERSION = "evaluation_bge_m3_text_v1"  # 标识评测侧独立且稳定的论文文本格式。


class BgeM3OfflineScorerError(RuntimeError):
    """表示本地 BGE-M3 路径、依赖、模型或编码输出不可用于评测。"""


class BgeM3OfflineOutOfMemoryError(BgeM3OfflineScorerError):
    """表示一次 BGE-M3 编码因 CPU/GPU 内存不足失败，可降批重试一次。"""


class LocalBgeM3Encoder(Protocol):
    """约束评测适配器所需的本地查询—文档 dense 打分能力。"""

    def score(self, query_text: str, document_texts: Sequence[str], *, batch_size: int) -> list[float]:
        """按输入论文顺序返回等长 dense 内积分数，不执行网络下载。"""
        ...  # 具体实现延迟加载用户已准备好的本地模型。


class _FlagEmbeddingLocalBgeM3Encoder:
    """延迟装配 FlagEmbedding，并只把已有本地目录传给 BGE-M3。"""

    def __init__(self, model_path: Path, device: str, use_fp16: bool) -> None:
        """保存已校验的本地模型路径和设备配置，不在构造时加载模型。"""
        self._model_path = model_path  # 保存调用方显式提供的本地目录。
        self._device = device  # 保存用户显式选择的实际推理设备。
        self._use_fp16 = use_fp16  # 保存是否使用半精度的本地运行选择。
        self._model: object | None = None  # 首次非空评分时才缓存模型实例。

    def score(self, query_text: str, document_texts: Sequence[str], *, batch_size: int) -> list[float]:
        """使用 BGE-M3 dense 向量计算单查询与候选论文的内积。"""
        if not document_texts:  # 空候选不应加载模型或占用本地设备。
            return []  # 返回与输入严格等长的空分数列表。
        model = self._get_model()  # 仅在用户实际执行评分时延迟加载本地模型。
        try:  # 统一映射第三方模型的设备和推理异常。
            query_vector = model.encode([query_text], batch_size=1, return_dense=True)["dense_vecs"][0]  # 单独编码查询以保持论文批大小可配置。
            document_vectors = model.encode(list(document_texts), batch_size=batch_size, return_dense=True)["dense_vecs"]  # 按传入顺序批量编码论文。
        except Exception as error:  # FlagEmbedding 可能抛出依赖、权重或内存异常。
            if _is_out_of_memory_error(error):  # 保留可由上层安全恢复的一次降批信号。
                raise BgeM3OfflineOutOfMemoryError("BGE-M3 本地编码内存不足") from error  # 不泄露底层路径或设备堆栈。
            raise BgeM3OfflineScorerError("BGE-M3 本地编码不可用") from error  # 向离线执行器提供稳定错误边界。
        return [_dot_product(query_vector, document_vector) for document_vector in document_vectors]  # 保持论文输入与分数的逐项对应。

    def _get_model(self) -> object:
        """首次评分时加载本地目录；目录预检阻止模型库回退到远程仓库。"""
        if self._model is not None:  # 同一适配器实例复用已加载的本地模型。
            return self._model  # 避免重复初始化和重复占用显存。
        try:  # 将可选依赖延迟到用户明确执行本地模型时导入。
            from FlagEmbedding import BGEM3FlagModel  # 复用项目已固定版本的官方 BGE-M3 实现。
            self._model = BGEM3FlagModel(str(self._model_path), use_fp16=self._use_fp16, device=self._device)  # 仅传入已存在的本地路径，不接受远程模型名。
        except Exception as error:  # 不向调用方传播模型目录、缓存或底层环境细节。
            raise BgeM3OfflineScorerError("BGE-M3 本地模型加载失败") from error  # 明确要求用户检查本地模型目录和依赖。
        return self._model  # 返回已缓存的 FlagEmbedding 模型实例。


def _create_flag_embedding_encoder(model_path: Path, device: str, use_fp16: bool) -> LocalBgeM3Encoder:
    """创建默认 FlagEmbedding 本地编码器，便于测试注入纯替身。"""
    return _FlagEmbeddingLocalBgeM3Encoder(model_path, device, use_fp16)  # 不在此处导入模型库或读取模型文件。


class BgeM3OfflineScorer:
    """将用户指定的本地 BGE-M3 模型适配为评测 `OfflineRankingScorer`。

    参数：
        model_path：用户已准备好的本地模型目录；必须包含 ``config.json``。
        model_name：写入评测审计记录的模型标识，不作为下载源。
        device：显式本地设备，只允许 ``cpu`` 或 ``cuda``。
        batch_size：首次论文编码批大小；内存不足时最多减半重试一次。
        use_fp16：是否请求 FlagEmbedding 使用半精度。
        encoder_factory：测试替身或其他本地编码器工厂。
    """

    def __init__(self, model_path: Path, *, model_name: str = "BAAI/bge-m3", device: str = "cpu", batch_size: int = 8, use_fp16: bool = False, encoder_factory: Callable[[Path, str, bool], LocalBgeM3Encoder] = _create_flag_embedding_encoder) -> None:
        """校验本地输入边界，但不导入、下载或加载任何模型。"""
        normalized_path = Path(model_path)  # 统一接受字符串或 Path 形式的本地路径。
        if not normalized_path.is_dir():  # 禁止远程仓库名、缺失路径或普通文件进入模型构造器。
            raise BgeM3OfflineScorerError("BGE-M3 评测模型路径必须是已存在的本地目录")  # 防止模型库隐式尝试联网解析路径。
        if not (normalized_path / "config.json").is_file():  # Hugging Face 本地模型最小配置文件缺失时不可安全执行。
            raise BgeM3OfflineScorerError("BGE-M3 评测模型目录缺少 config.json")  # 要求用户先完整准备模型而非运行时下载。
        if not model_name.strip():  # 审计记录必须携带可读模型标识。
            raise ValueError("model_name 不能为空")  # 在实际模型加载前拒绝无效元数据。
        if device not in {"cpu", "cuda"}:  # 评测结果必须精确记录实际设备。
            raise ValueError("device 必须为 cpu 或 cuda")  # 不接受会导致隐式设备选择的模糊值。
        if batch_size < 1:  # 零批大小无法形成有效的本地推理请求。
            raise ValueError("batch_size 必须大于零")  # 尽早报告配置错误。
        self._model_path = normalized_path  # 保存预检通过的本地模型目录。
        self._model_name = model_name.strip()  # 保存仅供审计的模型标识。
        self._device = device  # 保存显式用户设备选择。
        self._batch_size = batch_size  # 保存默认文档编码批大小。
        self._use_fp16 = use_fp16  # 保存半精度选项。
        self._encoder_factory = encoder_factory  # 保留可替换本地编码器边界。
        self._encoder: LocalBgeM3Encoder | None = None  # 推迟到首个非空评分任务才创建编码器。

    def score(self, query: str, query_intent: object, papers: Sequence[CandidatePaper]) -> RankingScoreBatch:
        """对已封存候选打分；不读取 QueryIntent 以外的外部状态或网络资源。"""
        del query_intent  # 当前确定性评测文本仅使用快照已冻结的查询正文，避免推测空约束。
        normalized_query = _normalize_text(query)  # 压缩查询中的无语义空白，保持与论文文本一致。
        if not normalized_query:  # 空白查询不能生成可解释的语义分数。
            raise ValueError("BGE-M3 评测查询不能为空")  # 避免向模型传递无内容输入。
        if not papers:  # 空快照允许生成零分数审计记录而不加载模型。
            return RankingScoreBatch(scores=[], model_name=self._audited_model_name, latency_ms=0.0, device=self._device, batch_size=self._batch_size)  # 明确本地模型未执行和文本格式版本。
        document_texts = [_build_document_text(paper) for paper in papers]  # 按候选快照顺序构造版本化论文文本。
        started_at = perf_counter()  # 从实际模型调用前开始统计本地评分耗时。
        encoder = self._get_encoder()  # 仅在非空候选时创建可替换本地编码器。
        active_batch_size = self._batch_size  # 保存首轮实际批大小用于审计。
        oom_retry_count = 0  # 默认不发生内存不足重试。
        try:  # 首次按用户指定批大小编码候选论文。
            scores = encoder.score(normalized_query, document_texts, batch_size=active_batch_size)  # 始终传递顺序稳定的本地文本。
        except BgeM3OfflineOutOfMemoryError:  # 仅处理适配器显式归类的可恢复内存不足。
            if active_batch_size == 1:  # 最小批仍失败时继续重试没有意义。
                raise  # 将清晰的本地资源错误交给用户处理。
            active_batch_size = max(1, active_batch_size // 2)  # 至多将批大小减半一次，避免无限重试。
            oom_retry_count = 1  # 冻结实际发生的一次降批次数。
            scores = encoder.score(normalized_query, document_texts, batch_size=active_batch_size)  # 仅重试一次相同候选和查询。
        if len(scores) != len(papers):  # 分数错位会破坏候选快照的公平比较。
            raise BgeM3OfflineScorerError("BGE-M3 评分数量与候选数量不一致")  # 在交给运行器前提前拒绝错误输出。
        return RankingScoreBatch(scores=[float(score) for score in scores], model_name=self._audited_model_name, latency_ms=(perf_counter() - started_at) * 1000.0, device=self._device, batch_size=active_batch_size, oom_retry_count=oom_retry_count)  # 返回包含文本格式版本的可审计本地模型统计。

    def _get_encoder(self) -> LocalBgeM3Encoder:
        """延迟创建本地编码器，确保构造适配器和空快照不加载模型。"""
        if self._encoder is None:  # 仅首个有候选的用户显式任务需要模型实例。
            self._encoder = self._encoder_factory(self._model_path, self._device, self._use_fp16)  # 工厂只能收到已校验的本地目录。
        return self._encoder  # 后续查询复用相同进程内模型。

    @property
    def _audited_model_name(self) -> str:
        """返回同时标识本地模型和论文文本格式版本的审计名称。"""
        return f"{self._model_name};text={BGE_M3_EVALUATION_TEXT_VERSION}"  # 避免结果归档无法区分文本字段策略变更。


def _build_document_text(paper: CandidatePaper) -> str:
    """构造版本化的 BGE-M3 论文文本，只使用候选快照中的公开结构化字段。"""
    title = _normalize_text(paper.title)  # 标题始终是缺摘要论文的最低语义依据。
    if not title:  # 防止仅空白的历史标题进入模型文本。
        raise BgeM3OfflineScorerError("BGE-M3 候选论文标题不能为空")  # 明确拒绝不可解释的候选输入。
    keywords = ", ".join(normalized for keyword in paper.keywords if (normalized := _normalize_text(keyword)))  # 保留快照中的关键词顺序并移除空项。
    abstract = _normalize_text(paper.abstract)  # 不引入来源原始 JSON 或未冻结字段。
    venue = _normalize_text(paper.venue or "") or "Unknown"  # 缺失 venue 使用稳定占位符而不猜测。
    year = str(paper.year) if paper.year is not None else "Unknown"  # 缺失年份同样使用明确稳定占位符。
    return "\n".join((f"Title: {title}", f"Keywords: {keywords}", f"Abstract: {abstract}", f"Venue: {venue}", f"Year: {year}"))  # 固定字段顺序使离线评分可复现。


def _normalize_text(value: str) -> str:
    """压缩 Unicode 连续空白，不改写文本语义或语言。"""
    return sub(r"\s+", " ", value).strip()  # 统一标题、摘要与查询中的换行、制表符和多余空格。


def _dot_product(first_vector: Sequence[object], second_vector: Sequence[object]) -> float:
    """计算维度严格一致的 dense 向量内积，拒绝模型异常形状。"""
    if len(first_vector) != len(second_vector):  # 维度不一致会使输出分数不可解释。
        raise BgeM3OfflineScorerError("BGE-M3 dense 向量维度不一致")  # 阻止截断向量静默参与排序。
    return sum(float(first_value) * float(second_value) for first_value, second_value in zip(first_vector, second_vector, strict=True))  # 逐维计算确定性 Python 浮点内积。


def _is_out_of_memory_error(error: Exception) -> bool:
    """识别常见 CPU/GPU 内存不足错误，不暴露底层运行时内容。"""
    message = str(error).casefold()  # 仅比较错误类别文本，不记录原始异常。
    return "out of memory" in message or "cuda oom" in message or "内存不足" in message  # 覆盖常见 PyTorch 与本地化运行时提示。
