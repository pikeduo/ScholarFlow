"""封装 BGE-M3 的懒加载、向量编码与余弦相似度计算边界。"""

from collections.abc import Sequence  # 声明模型适配器接受的稳定文本序列类型。
from time import perf_counter  # 记录本地模型首次加载耗时。
from typing import Protocol  # 声明可由测试替换的语义编码器协议。

from backend.app.core.logging import logger  # 记录模型开始加载、完成和失败堆栈。


class BgeM3EncoderError(RuntimeError):
    """表示 BGE-M3 依赖、模型加载或编码不可用的已净化错误。"""


class SemanticTextEncoder(Protocol):
    """约束语义粗排服务所需的最小查询-文档打分能力。"""

    def score(self, query_text: str, document_texts: Sequence[str]) -> list[float]:
        """为单条查询和多个文档返回等长的密集向量点积分数。"""
        ...  # Protocol 仅声明边界，不承担具体模型加载。


class BgeM3Encoder:
    """使用 FlagEmbedding 在首次实际排序时懒加载 BAAI/bge-m3。"""

    def __init__(self, model_name: str = "BAAI/bge-m3", use_fp16: bool = False) -> None:
        """保存模型配置，不在构造阶段下载模型或占用显存。"""
        self._model_name = model_name  # 保存可替换的 Hugging Face 模型名称。
        self._use_fp16 = use_fp16  # 保存是否在支持的设备上使用半精度推理。
        self._model: object | None = None  # 延迟保存首次使用时加载的 FlagEmbedding 模型实例。

    def score(self, query_text: str, document_texts: Sequence[str]) -> list[float]:
        """编码查询和文档，并返回 BGE-M3 dense 向量的内积分数。"""
        if not document_texts:  # 空候选无需加载模型或计算向量。
            return []  # 返回与输入等长的空分数列表。
        model = self._get_model()  # 仅在实际存在候选时加载或复用 BGE-M3。
        query_vector = model.encode([query_text], return_dense=True)["dense_vecs"][0]  # 生成单条查询的 dense 向量。
        document_vectors = model.encode(list(document_texts), return_dense=True)["dense_vecs"]  # 批量生成候选论文的 dense 向量。
        return [sum(float(query_value) * float(document_value) for query_value, document_value in zip(query_vector, document_vector, strict=True)) for document_vector in document_vectors]  # 计算长度一致 dense 向量的稳定点积分数。

    def _get_model(self) -> object:
        """首次需要时加载 BGE-M3，并将依赖或模型错误映射为安全异常。"""
        if self._model is not None:  # 已加载模型可被同一服务实例复用。
            return self._model  # 避免重复下载、初始化和占用显存。
        started_at = perf_counter()  # 从依赖导入前开始统计下载或缓存加载耗时。
        logger.info("BGE-M3 模型开始加载：模型=%s", self._model_name)  # 首次下载期间即使长时间无输出也能定位当前阶段。
        try:  # 将可选依赖导入延后到真正执行模型推理的时刻。
            from FlagEmbedding import BGEM3FlagModel  # 使用官方 BGE-M3 dense 编码实现。
            self._model = BGEM3FlagModel(self._model_name, use_fp16=self._use_fp16)  # 首次调用时按配置加载本地或缓存模型。
        except Exception as error:  # 统一隐藏依赖路径、下载地址和底层运行时细节。
            logger.exception("BGE-M3 模型加载失败：模型=%s，耗时=%.3f秒", self._model_name, perf_counter() - started_at)  # 在受控日志中保留下载或设备错误堆栈。
            raise BgeM3EncoderError("BGE-M3 语义模型不可用") from error  # 向业务层提供稳定安全的降级边界。
        logger.info("BGE-M3 模型加载完成：模型=%s，耗时=%.3f秒", self._model_name, perf_counter() - started_at)  # 记录缓存或下载完成时间。
        return self._model  # 返回完成初始化的模型对象。
