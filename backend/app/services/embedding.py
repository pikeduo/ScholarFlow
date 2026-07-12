"""提供 BGE-M3 批量嵌入、设备选择、归一化与可解释降级边界。"""

import asyncio  # 将同步本地模型推理移出 FastAPI 事件循环。
from dataclasses import dataclass  # 使用轻量不可变结果对象表达稳定服务契约。
from math import sqrt  # 使用标准库计算向量 L2 范数，避免新增直接依赖。
from time import perf_counter  # 记录批量编码耗时供后续用量统计复用。
from typing import Literal  # 限制公开设备配置取值。

from backend.app.adapters.bge_m3 import BgeM3Encoder, BgeM3EncoderError, BgeM3OutOfMemoryError, DenseTextEncoder, EmbeddingDeviceResolver, TorchEmbeddingDeviceResolver  # 仅依赖可替换的模型与设备适配边界。
from backend.app.core.logging import logger  # 记录不含论文或查询原文的模型运行统计。


EmbeddingDevicePreference = Literal["auto", "cpu", "cuda"]  # 限制配置层支持的设备选择策略。


class EmbeddingServiceError(RuntimeError):
    """表示批量嵌入服务不可用、输入向量异常或设备不可满足的安全错误。"""


@dataclass(frozen=True)
class EmbeddingServiceConfig:
    """保存 EmbeddingService 的模型与运行策略。

    属性：
        model_name：BGE-M3 模型标识或本地模型目录。
        model_revision：可选权重修订，供后续索引元数据记录。
        device：自动、CPU 或 CUDA 的设备选择偏好。
        minimum_cuda_memory_mb：自动选择 GPU 所需的最小总显存。
        batch_size_cpu：CPU 推理的保守批量大小。
        batch_size_gpu：GPU 推理的默认批量大小。
        normalize：是否将输出向量归一化为余弦检索可用的单位向量。
    """

    model_name: str = "BAAI/bge-m3"  # 使用规划指定的默认 BGE-M3 模型。
    model_revision: str | None = None  # 允许未来配置精确模型版本。
    device: EmbeddingDevicePreference = "auto"  # 默认按硬件能力自动选择运行设备。
    minimum_cuda_memory_mb: int = 4096  # 避免低显存 GPU 在长文本批处理时频繁 OOM。
    batch_size_cpu: int = 4  # 限制 CPU 并行压力，避免占满开发机。
    batch_size_gpu: int = 16  # 为 GPU 推理提供规划建议的默认批量。
    normalize: bool = True  # 默认输出单位向量供内积索引等价实现余弦相似度。

    def __post_init__(self) -> None:
        """校验模型标识、设备门槛与批处理配置。"""
        if not self.model_name.strip():  # 空模型标识无法装配默认适配器。
            raise ValueError("model_name 不能为空")  # 在服务构造前返回稳定配置错误。
        if self.device not in ("auto", "cpu", "cuda"):  # dataclass 不会像 Pydantic 一样在运行时自动校验 Literal。
            raise ValueError("device 必须为 auto、cpu 或 cuda")  # 防止错误设备文本被静默解释为 CPU。
        if self.minimum_cuda_memory_mb < 1:  # 零门槛会失去自动 GPU 保护意义。
            raise ValueError("minimum_cuda_memory_mb 必须大于零")  # 保证资源判断有效。
        if self.batch_size_cpu < 1 or self.batch_size_gpu < 1:  # 所有实际批量都必须为正。
            raise ValueError("批量大小必须大于零")  # 防止将无效配置传给第三方模型。


@dataclass(frozen=True)
class EmbeddingBatch:
    """保存一次批量编码的标准化向量与安全运行元数据。"""

    vectors: tuple[tuple[float, ...], ...]  # 保存与输入文本顺序一致的不可变向量集合。
    model_name: str  # 记录实际模型标识，供索引元数据和可观测性使用。
    model_revision: str | None  # 记录可选模型权重修订。
    dimension: int  # 记录向量维度，空输入时为零。
    normalized: bool  # 标记结果是否已经进行 L2 归一化。
    latency_ms: int  # 记录端到端编码耗时的毫秒整数。
    device: str  # 记录实际使用的设备，空输入时为 not_used。


class EmbeddingService:
    """在受控线程中执行 BGE-M3 编码，并输出可供 FAISS 使用的单位向量。

    参数：
        config：模型、设备和批量策略配置。
        encoder：可选文本编码器替身；省略时首次实际编码才创建 BGE-M3 适配器。
        device_resolver：可选设备解析器替身；省略时使用 PyTorch 解析器。
    """

    def __init__(self, config: EmbeddingServiceConfig | None = None, encoder: DenseTextEncoder | None = None, device_resolver: EmbeddingDeviceResolver | None = None) -> None:
        """保存可替换依赖，不加载模型、不探测 CUDA 且不创建线程。"""
        self._config = config or EmbeddingServiceConfig()  # 保存默认或由组合根注入的运行策略。
        self._encoder = encoder  # 延迟创建默认 BGE-M3 适配器以遵守首次使用懒加载。
        self._device_resolver = device_resolver or TorchEmbeddingDeviceResolver()  # 允许单元测试绕过真实 PyTorch 探测。
        self._resolved_device: str | None = None  # 缓存首次实际编码得到的设备，避免运行中切换。

    async def encode_queries(self, texts: list[str]) -> EmbeddingBatch:
        """编码查询文本，返回顺序一致且可用于内积检索的向量批次。"""
        return await self._encode(texts, operation="query")  # 统一复用批量策略与安全错误边界。

    async def encode_documents(self, texts: list[str]) -> EmbeddingBatch:
        """编码论文或文献库文档文本，返回顺序一致且可用于 FAISS 的向量批次。"""
        return await self._encode(texts, operation="document")  # 保持与查询编码相同的模型和归一化策略。

    async def _encode(self, texts: list[str], operation: str) -> EmbeddingBatch:
        """在受控线程执行同步模型编码，避免阻塞异步 API 请求处理。"""
        if not texts:  # 空批次不应探测硬件、加载模型或产生错误日志。
            return EmbeddingBatch(vectors=(), model_name=self._config.model_name, model_revision=self._config.model_revision, dimension=0, normalized=self._config.normalize, latency_ms=0, device="not_used")  # 返回可供调用方直接消费的稳定空契约。
        if any(not text.strip() for text in texts):  # 空白文本没有可解释的语义向量。
            raise EmbeddingServiceError("嵌入文本不能为空")  # 在模型调用前阻止无效输入污染索引。
        started_at = perf_counter()  # 从设备解析前开始统计一次完整编码耗时。
        try:  # 统一映射适配器、设备与向量形状错误。
            device = self._get_device()  # 首次编码时确定并缓存实际设备。
            batch_size = self._batch_size_for(device)  # 按实际设备选择受控批量大小。
            encoder = self._get_encoder(device)  # 首次编码时才创建默认模型适配器。
            vectors = await asyncio.to_thread(self._encode_with_oom_retry, encoder, texts, batch_size)  # 将同步推理移到线程避免阻塞事件循环。
            normalized_vectors, dimension = _validate_and_normalize(vectors, self._config.normalize)  # 校验维度并按配置生成单位向量。
        except BgeM3EncoderError as error:  # 适配器已将底层依赖和模型错误净化。
            logger.exception("BGE-M3 批量嵌入失败：操作=%s，文本数=%d", operation, len(texts))  # 仅记录类型和数量，不记录用户查询或论文内容。
            raise EmbeddingServiceError("BGE-M3 嵌入模型不可用") from error  # 向 API 层隐藏设备、路径和底层响应。
        except ValueError as error:  # 将不规则向量或配置错误映射为稳定业务边界。
            logger.exception("BGE-M3 嵌入结果无效：操作=%s，文本数=%d", operation, len(texts))  # 保留受控堆栈用于诊断。
            raise EmbeddingServiceError("BGE-M3 嵌入结果无效") from error  # 不向调用方暴露内部向量内容。
        latency_ms = int((perf_counter() - started_at) * 1000)  # 在成功路径记录可观测的总耗时。
        result = EmbeddingBatch(vectors=normalized_vectors, model_name=self._config.model_name, model_revision=self._config.model_revision, dimension=dimension, normalized=self._config.normalize, latency_ms=latency_ms, device=device)  # 构造稳定返回契约。
        logger.info("BGE-M3 批量嵌入完成：操作=%s，文本数=%d，维度=%d，设备=%s，耗时毫秒=%d", operation, len(texts), result.dimension, result.device, result.latency_ms)  # 记录后续用量统计需要的非敏感指标。
        return result  # 返回可直接交给索引层的批量结果。

    def _get_device(self) -> str:
        """首次需要时解析并缓存运行设备。"""
        if self._resolved_device is None:  # 避免每批次重新探测 GPU 状态。
            self._resolved_device = self._device_resolver.resolve(self._config.device, self._config.minimum_cuda_memory_mb)  # 通过适配器隔离 torch 与硬件细节。
        return self._resolved_device  # 返回同一服务生命周期内稳定的实际设备。

    def _get_encoder(self, device: str) -> DenseTextEncoder:
        """返回测试注入或按实际设备懒创建的 BGE-M3 编码器。"""
        if self._encoder is None:  # 默认适配器仅在首个非空批次才创建。
            self._encoder = BgeM3Encoder(model_name=self._config.model_name, use_fp16=device == "cuda", device=device)  # CUDA 默认使用半精度，CPU 保持兼容精度。
        return self._encoder  # 复用同一模型实例避免重复加载权重。

    def _batch_size_for(self, device: str) -> int:
        """根据实际设备返回对应的配置批量大小。"""
        return self._config.batch_size_gpu if device == "cuda" else self._config.batch_size_cpu  # 未知设备保守按 CPU 批量运行。

    @staticmethod
    def _encode_with_oom_retry(encoder: DenseTextEncoder, texts: list[str], batch_size: int) -> list[list[float]]:
        """执行一次编码；仅在内存不足时以减半批量重试一次。"""
        try:  # 首先按目标设备的正常批量执行。
            return encoder.encode(texts, batch_size=batch_size)  # 保持编码器输出顺序与输入文本一致。
        except BgeM3OutOfMemoryError:  # 仅对明确可恢复的资源错误执行一次重试。
            retry_batch_size = max(1, batch_size // 2)  # 不允许减半后出现零批量。
            if retry_batch_size == batch_size:  # 原批量已经为一时继续重试没有意义。
                raise  # 保留原始已净化错误供调用方返回降级。
            logger.warning("BGE-M3 编码降批重试：原批量=%d，重试批量=%d，文本数=%d", batch_size, retry_batch_size, len(texts))  # 记录资源调整，不记录文本。
            return encoder.encode(texts, batch_size=retry_batch_size)  # 只允许一次降批，避免无限资源重试。


def _validate_and_normalize(vectors: list[list[float]], normalize: bool) -> tuple[tuple[tuple[float, ...], ...], int]:
    """校验向量数量和维度，并按配置生成不可变单位向量。"""
    if not vectors:  # 非空输入却没有向量表示编码器返回无效形状。
        raise ValueError("编码器未返回向量")  # 防止调用方误将空结果写入索引。
    dimension = len(vectors[0])  # 以第一条向量确定当前批次的维度。
    if dimension < 1:  # 零维向量不能建立内积索引。
        raise ValueError("向量维度必须大于零")  # 明确拒绝模型或替身的异常输出。
    output: list[tuple[float, ...]] = []  # 收集已校验且可选归一化的不可变向量。
    for vector in vectors:  # 保持原始文本与向量的顺序一一对应。
        if len(vector) != dimension:  # 不接受批次内混合维度。
            raise ValueError("向量维度不一致")  # 防止后续 FAISS add 操作失败。
        values = tuple(float(value) for value in vector)  # 统一数值类型，隔离 NumPy 标量等第三方表示。
        norm = sqrt(sum(value * value for value in values))  # 使用 L2 范数准备余弦相似度所需单位向量。
        if norm == 0.0:  # 零向量无法归一化且没有检索语义。
            raise ValueError("向量范数不能为零")  # 阻止无效向量进入缓存或索引。
        output.append(tuple(value / norm for value in values) if normalize else values)  # 仅在配置启用时输出单位向量。
    return tuple(output), dimension  # 返回稳定不可变输出与后续索引所需维度。
