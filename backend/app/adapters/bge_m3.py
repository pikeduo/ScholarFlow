"""封装 BGE-M3 的懒加载、向量编码与余弦相似度计算边界。"""

from collections.abc import Sequence  # 声明模型适配器接受的稳定文本序列类型。
from time import perf_counter  # 记录本地模型首次加载耗时。
from typing import Protocol  # 声明可由测试替换的语义编码器协议。

from backend.app.core.logging import logger  # 记录模型开始加载、完成和失败堆栈。


class BgeM3EncoderError(RuntimeError):
    """表示 BGE-M3 依赖、模型加载或编码不可用的已净化错误。"""


class BgeM3OutOfMemoryError(BgeM3EncoderError):
    """表示 BGE-M3 推理发生显存或内存不足，可由业务层降批重试。"""


class SemanticTextEncoder(Protocol):
    """约束语义粗排服务所需的最小查询-文档打分能力。"""

    def score(self, query_text: str, document_texts: Sequence[str]) -> list[float]:
        """为单条查询和多个文档返回等长的密集向量点积分数。"""
        ...  # Protocol 仅声明边界，不承担具体模型加载。


class DenseTextEncoder(Protocol):
    """约束批量嵌入服务需要的最小文本编码能力。"""

    def encode(self, texts: Sequence[str], *, batch_size: int) -> list[list[float]]:
        """将文本批量转换为等维的 dense 向量。"""
        ...  # Protocol 仅声明稳定输入输出，不暴露 FlagEmbedding 返回结构。


class EmbeddingDeviceResolver(Protocol):
    """约束嵌入服务需要的设备选择能力，测试可注入替身。"""

    def resolve(self, preference: str, minimum_cuda_memory_mb: int) -> str:
        """根据配置偏好返回实际使用的 cpu 或 cuda 设备名称。"""
        ...  # Protocol 不在业务层引入 torch 依赖。


class TorchEmbeddingDeviceResolver:
    """通过 PyTorch 在首次编码前选择满足显存门槛的设备。"""

    def resolve(self, preference: str, minimum_cuda_memory_mb: int) -> str:
        """解析显式或自动设备配置，自动模式在 GPU 不可用时稳定回退 CPU。"""
        if preference == "cpu":  # 用户明确要求 CPU 时无需导入或探测 CUDA。
            return "cpu"  # 保持显式配置优先。
        try:  # 将 torch 导入延后到真正需要选择设备的时刻。
            import torch  # FlagEmbedding 运行时通常已携带 PyTorch 依赖。
        except Exception as error:  # 未安装 torch 时只能支持明确 CPU 的无模型测试路径。
            if preference == "auto":  # 自动模式可安全回退到 CPU，由模型加载阶段给出依赖错误。
                return "cpu"  # 避免因仅探测设备而提前中断服务。
            raise BgeM3EncoderError("BGE-M3 CUDA 设备不可用") from error  # 显式 CUDA 不应静默改为 CPU。
        cuda_available = torch.cuda.is_available()  # 检查当前运行时是否可访问 CUDA。
        if not cuda_available:  # 没有可用 GPU 时仅自动模式允许回退。
            if preference == "auto":  # 遵守自动选择的 CPU 回退语义。
                return "cpu"  # 返回实际设备用于日志和响应元数据。
            raise BgeM3EncoderError("BGE-M3 CUDA 设备不可用")  # 显式 CUDA 请求应获得明确错误。
        total_memory_mb = int(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024))  # 读取第一张 GPU 的总显存作为保守门槛。
        if preference == "cuda":  # 用户明确指定 GPU 时不因门槛配置拒绝其选择。
            return "cuda"  # 将实际设备交给模型适配器。
        return "cuda" if total_memory_mb >= minimum_cuda_memory_mb else "cpu"  # 自动模式仅在资源满足门槛时启用 GPU。


class BgeM3Encoder:
    """使用 FlagEmbedding 在首次实际排序时懒加载 BAAI/bge-m3。"""

    def __init__(self, model_name: str = "BAAI/bge-m3", use_fp16: bool = False, device: str | None = None, device_preference: str = "auto", minimum_cuda_memory_mb: int = 4096, device_resolver: EmbeddingDeviceResolver | None = None) -> None:
        """保存模型配置，不在构造阶段下载模型或占用显存。"""
        if device is None and device_preference not in {"auto", "cpu", "cuda"}:  # 未传入实际设备时必须校验公开设备偏好。
            raise ValueError("device_preference 必须为 auto、cpu 或 cuda")  # 防止错误文本被静默回退到 CPU。
        if minimum_cuda_memory_mb < 1:  # 零显存门槛会破坏自动设备选择的资源保护。
            raise ValueError("minimum_cuda_memory_mb 必须大于零")  # 在模型加载前拒绝不安全配置。
        self._model_name = model_name  # 保存可替换的 Hugging Face 模型名称。
        self._use_fp16 = use_fp16  # 保存是否在兼容设备上使用半精度推理。
        self._device = device  # 保存调用方已解析的设备，非空时优先于自动策略。
        self._device_preference = device_preference  # 保存 auto、cpu 或 cuda 的延迟解析偏好。
        self._minimum_cuda_memory_mb = minimum_cuda_memory_mb  # 保存自动使用 CUDA 所需的显存门槛。
        self._device_resolver = device_resolver or TorchEmbeddingDeviceResolver()  # 复用可替换设备解析边界，测试不依赖真实 CUDA。
        self._resolved_device: str | None = device  # 缓存第一次解析出的实际设备，禁止运行中切换模型设备。
        self._model: object | None = None  # 延迟保存首次使用时加载的 FlagEmbedding 模型实例。

    def score(self, query_text: str, document_texts: Sequence[str]) -> list[float]:
        """编码查询和文档，并返回 BGE-M3 dense 向量的内积分数。"""
        if not document_texts:  # 空候选无需加载模型或计算向量。
            return []  # 返回与输入等长的空分数列表。
        query_vector = self.encode([query_text], batch_size=1)[0]  # 生成单条查询的 dense 向量。
        document_vectors = self.encode(document_texts, batch_size=len(document_texts))  # 批量生成候选论文的 dense 向量。
        return [sum(float(query_value) * float(document_value) for query_value, document_value in zip(query_vector, document_vector, strict=True)) for document_vector in document_vectors]  # 计算长度一致 dense 向量的稳定点积分数。

    def encode(self, texts: Sequence[str], *, batch_size: int) -> list[list[float]]:
        """将文本批量编码为 Python 浮点向量，隔离 FlagEmbedding 响应结构。

        参数：
            texts：待编码的非空或空文本序列。
            batch_size：单次模型推理的最大文本数。
        返回：
            list[list[float]]：顺序与输入一致的 dense 向量列表。
        异常：
            BgeM3OutOfMemoryError：推理内存不足时抛出，供上层降批一次。
            BgeM3EncoderError：模型或推理不可用时抛出。
        """
        if not texts:  # 空输入不应触发模型加载或设备占用。
            return []  # 返回稳定空结果供调用方直接处理。
        if batch_size < 1:  # 防止模型调用收到无效的批大小。
            raise ValueError("batch_size 必须大于零")  # 将编程配置错误直接暴露给调用方。
        model = self._get_model()  # 仅在实际存在文本时加载或复用 BGE-M3。
        try:  # 将第三方推理错误统一映射为稳定适配器错误。
            dense_vectors = model.encode(list(texts), batch_size=batch_size, return_dense=True)["dense_vecs"]  # 调用官方 dense 向量输出并保留输入顺序。
        except Exception as error:  # 模型运行时可能抛出 CUDA、内存或权重错误。
            if _is_out_of_memory_error(error):  # 单独保留可恢复的内存不足信号。
                logger.exception("BGE-M3 编码内存不足：模型=%s，批量=%d", self._model_name, batch_size)  # 记录不含原文的完整堆栈。
                raise BgeM3OutOfMemoryError("BGE-M3 编码内存不足") from error  # 允许上层只降低一次批大小。
            logger.exception("BGE-M3 编码失败：模型=%s，批量=%d", self._model_name, batch_size)  # 在受控日志保留运行时错误。
            raise BgeM3EncoderError("BGE-M3 语义编码不可用") from error  # 向业务层隐藏底层路径和设备细节。
        return [[float(value) for value in vector] for vector in dense_vectors]  # 转换为与 NumPy 实现解耦的标准 Python 浮点列表。

    def _get_model(self) -> object:
        """首次需要时加载 BGE-M3，并将依赖或模型错误映射为安全异常。"""
        if self._model is not None:  # 已加载模型可被同一服务实例复用。
            return self._model  # 避免重复下载、初始化和占用显存。
        device = self._resolve_device()  # 在首次真实加载前根据配置和硬件能力确定唯一设备。
        started_at = perf_counter()  # 从依赖导入前开始统计下载或缓存加载耗时。
        logger.info("BGE-M3 模型开始加载：模型=%s，设备=%s", self._model_name, device)  # 首次下载期间即使长时间无输出也能定位当前阶段和实际设备。
        try:  # 将可选依赖导入延后到真正执行模型推理的时刻。
            from FlagEmbedding import BGEM3FlagModel  # 使用官方 BGE-M3 dense 编码实现。
            self._model = BGEM3FlagModel(self._model_name, use_fp16=self._use_fp16 or device == "cuda", device=device)  # CUDA 自动启用半精度以减少显存占用并提升推理速度。
        except Exception as error:  # 统一隐藏依赖路径、下载地址和底层运行时细节。
            logger.exception("BGE-M3 模型加载失败：模型=%s，设备=%s，耗时=%.3f秒", self._model_name, device, perf_counter() - started_at)  # 在受控日志中保留下载或设备错误堆栈。
            raise BgeM3EncoderError("BGE-M3 语义模型不可用") from error  # 向业务层提供稳定安全的降级边界。
        logger.info("BGE-M3 模型加载完成：模型=%s，设备=%s，耗时=%.3f秒", self._model_name, device, perf_counter() - started_at)  # 记录缓存或下载完成时间。
        return self._model  # 返回完成初始化的模型对象。

    def _resolve_device(self) -> str:
        """返回模型生命周期内唯一且已验证的实际推理设备。"""
        if self._resolved_device is None:  # 调用方未传入实际设备时才需要按偏好探测硬件。
            self._resolved_device = self._device_resolver.resolve(self._device_preference, self._minimum_cuda_memory_mb)  # 自动模式优先 CUDA，显式 CUDA 不可用时返回已净化异常。
        return self._resolved_device  # 返回缓存后的 cpu 或 cuda，确保权重与推理位于同一设备。


def _is_out_of_memory_error(error: Exception) -> bool:
    """根据常见运行时错误文本识别可恢复的 CPU/GPU 内存不足。"""
    message = str(error).casefold()  # 仅比较错误描述，不记录可能包含路径的原始异常。
    return "out of memory" in message or "内存不足" in message or "cuda oom" in message  # 覆盖 PyTorch 与部分本地化运行时的常见报错。
