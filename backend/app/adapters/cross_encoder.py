"""封装 BGE Cross Encoder 的懒加载与查询-论文成对打分边界。"""

from collections.abc import Sequence  # 声明重排器接受的稳定文档文本序列类型。
from importlib.metadata import PackageNotFoundError, version as distribution_version  # 在加载权重前检测 FlagEmbedding 所依赖的 Transformers 主版本。
from time import perf_counter  # 记录本地重排模型首次加载耗时。
from typing import Protocol  # 声明可由单元测试替换的重排器协议。

from backend.app.adapters.bge_m3 import BgeM3EncoderError, EmbeddingDeviceResolver, TorchEmbeddingDeviceResolver  # 复用与 BGE-M3 一致的 CUDA 选择和显存保护边界。
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

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", use_fp16: bool = False, device_preference: str = "auto", minimum_cuda_memory_mb: int = 4096, device_resolver: EmbeddingDeviceResolver | None = None) -> None:
        """保存模型配置，不在构造阶段下载模型或初始化设备。"""
        if device_preference not in {"auto", "cpu", "cuda"}:  # Cross Encoder 仅接受与 BGE-M3 相同的公开设备策略。
            raise ValueError("device_preference 必须为 auto、cpu 或 cuda")  # 防止拼写错误被 FlagEmbedding 静默接受。
        if minimum_cuda_memory_mb < 1:  # 自动设备策略必须保留正的显存保护门槛。
            raise ValueError("minimum_cuda_memory_mb 必须大于零")  # 在模型加载前拒绝无效资源配置。
        self._model_name = model_name  # 保存可替换的 Hugging Face reranker 名称。
        self._use_fp16 = use_fp16  # 保存是否在兼容设备上启用半精度。
        self._device_preference = device_preference  # 保存 auto、cpu 或 cuda 的延迟解析偏好。
        self._minimum_cuda_memory_mb = minimum_cuda_memory_mb  # 保存自动使用 CUDA 所需的最小总显存。
        self._device_resolver = device_resolver or TorchEmbeddingDeviceResolver()  # 复用可替换的 PyTorch 硬件探测边界。
        self._resolved_device: str | None = None  # 缓存第一次解析出的模型实际设备。
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
        device = self._resolve_device()  # 在加载权重前统一确定 Cross Encoder 实际设备。
        started_at = perf_counter()  # 从依赖导入前开始统计下载或缓存加载耗时。
        logger.info("Cross Encoder 模型开始加载：模型=%s，设备=%s", self._model_name, device)  # 为首次下载和磁盘加载提供明确阶段标记与设备审计。
        try:  # 延迟导入避免离线测试因为可选模型依赖而失败。
            _ensure_flag_embedding_transformers_compatibility()  # 在加载权重前阻止已知不兼容的 Transformers 主版本。
            from FlagEmbedding import FlagReranker  # 使用官方 FlagEmbedding Cross Encoder 接口。
            self._model = FlagReranker(self._model_name, use_fp16=self._use_fp16 or device == "cuda", devices=device)  # CUDA 自动启用半精度，并显式传递设备避免库默认回退 CPU。
        except CrossEncoderError:  # 已知依赖组合不兼容时保留明确的安全诊断。
            logger.exception("Cross Encoder 依赖不兼容：模型=%s，设备=%s，耗时=%.3f秒", self._model_name, device, perf_counter() - started_at)  # 记录版本边界而不输出用户查询或论文文本。
            raise  # 让服务层继续执行既有安全降级。
        except Exception as error:  # 不向上层暴露模型地址、缓存路径或设备异常。
            logger.exception("Cross Encoder 模型加载失败：模型=%s，设备=%s，耗时=%.3f秒", self._model_name, device, perf_counter() - started_at)  # 在受控日志保留下载或设备错误堆栈。
            raise CrossEncoderError("Cross Encoder 重排模型不可用") from error  # 提供可降级的稳定业务错误。
        logger.info("Cross Encoder 模型加载完成：模型=%s，设备=%s，耗时=%.3f秒", self._model_name, device, perf_counter() - started_at)  # 记录首次加载完成时间。
        return self._model  # 返回完成初始化的 Cross Encoder 模型。

    def _resolve_device(self) -> str:
        """解析并缓存 Cross Encoder 的唯一实际设备，将 CUDA 探测错误映射为本适配器异常。"""
        if self._resolved_device is None:  # 首次加载前才触发硬件探测，避免构造服务时导入 torch。
            try:  # 复用 BGE-M3 同一套 auto、cpu、cuda 策略。
                self._resolved_device = self._device_resolver.resolve(self._device_preference, self._minimum_cuda_memory_mb)  # 自动模式在无合格 GPU 时安全回退 CPU。
            except BgeM3EncoderError as error:  # 显式 CUDA 不可用时转换为 Cross Encoder 自己的稳定边界。
                raise CrossEncoderError("Cross Encoder CUDA 设备不可用") from error  # 避免向上层泄露 PyTorch 或驱动细节。
        return self._resolved_device  # 返回缓存后的 cpu 或 cuda，保证权重与打分设备一致。


def _ensure_flag_embedding_transformers_compatibility() -> None:
    """校验当前 FlagEmbedding 1.4.x 所需的 Transformers 主版本，避免加载后才触发已移除接口。"""
    try:  # 只读取已安装分发包元数据，不导入模型、不下载权重。
        installed_version = distribution_version("transformers")  # 获取当前实际生效的 Transformers 版本。
    except PackageNotFoundError as error:  # 缺少 Transformers 时 FlagEmbedding 无法执行 tokenizer 推理。
        raise CrossEncoderError("Cross Encoder 缺少 Transformers 依赖，请安装 requirements.txt") from error  # 返回不暴露环境路径的可操作错误。
    major_version_text = installed_version.split(".", maxsplit=1)[0]  # 仅使用主版本判断已知公共 API 兼容边界。
    if not major_version_text.isdigit() or int(major_version_text) >= 5:  # Transformers 5 已移除 FlagEmbedding 1.4.x 仍调用的 tokenizer 接口。
        raise CrossEncoderError("Cross Encoder 与当前 Transformers 版本不兼容，请安装 requirements.txt 中锁定的版本")  # 让日志和服务降级说明指向可执行修复。
