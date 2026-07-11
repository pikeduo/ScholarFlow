"""封装 BGE Cross Encoder 的懒加载与查询-论文成对打分边界。"""

from collections.abc import Sequence  # 声明重排器接受的稳定文档文本序列类型。
from time import perf_counter  # 记录本地重排模型首次加载耗时。
from typing import Protocol  # 声明可由单元测试替换的重排器协议。

from backend.app.core.logging import logger  # 记录模型开始加载、完成和失败堆栈。


class CrossEncoderError(RuntimeError):
    """表示 Cross Encoder 依赖、模型加载或打分不可用的已净化错误。"""


class CrossEncoderScorer(Protocol):
    """约束 Cross Encoder 重排服务所需的最小成对打分能力。"""

    def score(self, query_text: str, document_texts: Sequence[str]) -> list[float]:
        """为单条查询与多个论文文本返回等长的精细相关性分数。"""
        ...  # Protocol 仅定义业务边界，不承担模型初始化。


class BgeCrossEncoder:
    """使用 FlagEmbedding 的 BGE reranker 在首次实际重排时懒加载模型。"""

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", use_fp16: bool = False) -> None:
        """保存模型配置，不在构造阶段下载模型或初始化设备。"""
        self._model_name = model_name  # 保存可替换的 Hugging Face reranker 名称。
        self._use_fp16 = use_fp16  # 保存是否在兼容设备上启用半精度。
        self._model: object | None = None  # 延迟保存首次真实调用时加载的 reranker 实例。

    def score(self, query_text: str, document_texts: Sequence[str]) -> list[float]:
        """对查询和论文文本对进行批量 Cross Encoder 打分。"""
        if not document_texts:  # 空候选无需加载模型或构造文本对。
            return []  # 返回与输入保持一致的空分数列表。
        model = self._get_model()  # 仅在确有候选时加载或复用本地 reranker。
        pairs = [[query_text, document_text] for document_text in document_texts]  # 构造 FlagEmbedding 所需的查询-文档成对输入。
        try:  # 将模型推理异常转换为稳定的服务层降级边界。
            scores = model.compute_score(pairs, normalize=True)  # 使用归一化分数便于跨请求展示和排序。
        except Exception as error:  # 屏蔽设备、权重路径或底层推理细节。
            raise CrossEncoderError("Cross Encoder 重排模型不可用") from error  # 返回不泄露内部细节的稳定错误。
        if isinstance(scores, (int, float)):  # 兼容库在单个候选时返回标量的行为。
            return [float(scores)]  # 统一为服务层使用的分数列表。
        return [float(score) for score in scores]  # 将批量分数转换为稳定浮点列表。

    def _get_model(self) -> object:
        """首次需要时加载 FlagEmbedding reranker，并映射依赖或模型错误。"""
        if self._model is not None:  # 已加载模型在同一服务实例内可复用。
            return self._model  # 避免重复下载、初始化和显存占用。
        started_at = perf_counter()  # 从依赖导入前开始统计下载或缓存加载耗时。
        logger.info("Cross Encoder 模型开始加载：模型=%s", self._model_name)  # 为首次下载和磁盘加载提供明确阶段标记。
        try:  # 延迟导入避免离线测试因为可选模型依赖而失败。
            from FlagEmbedding import FlagReranker  # 使用官方 FlagEmbedding Cross Encoder 接口。
            self._model = FlagReranker(self._model_name, use_fp16=self._use_fp16)  # 在首次真实重排时加载缓存或下载模型。
        except Exception as error:  # 不向上层暴露模型地址、缓存路径或设备异常。
            logger.exception("Cross Encoder 模型加载失败：模型=%s，耗时=%.3f秒", self._model_name, perf_counter() - started_at)  # 在受控日志保留下载或设备错误堆栈。
            raise CrossEncoderError("Cross Encoder 重排模型不可用") from error  # 提供可降级的稳定业务错误。
        logger.info("Cross Encoder 模型加载完成：模型=%s，耗时=%.3f秒", self._model_name, perf_counter() - started_at)  # 记录首次加载完成时间。
        return self._model  # 返回完成初始化的 Cross Encoder 模型。
