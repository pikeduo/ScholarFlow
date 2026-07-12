"""验证批量嵌入的设备策略、归一化、OOM 降批与安全错误边界。"""

import asyncio  # 使用标准库运行异步服务接口，无需 pytest 异步插件。

import pytest  # 提供异常和配置边界断言。

from backend.app.adapters.bge_m3 import BgeM3EncoderError, BgeM3OutOfMemoryError  # 构造不依赖真实模型的可控失败。
from backend.app.services.embedding import EmbeddingService, EmbeddingServiceConfig, EmbeddingServiceError  # 导入待测服务与公开配置契约。


class _StubDeviceResolver:
    """返回固定设备并记录服务传入的自动选择参数。"""

    def __init__(self, device: str) -> None:
        """保存测试期望的实际设备。"""
        self.device = device  # 保存无需 PyTorch 的固定设备结果。
        self.calls: list[tuple[str, int]] = []  # 记录调用参数供断言验证。

    def resolve(self, preference: str, minimum_cuda_memory_mb: int) -> str:
        """记录配置并返回预设实际设备。"""
        self.calls.append((preference, minimum_cuda_memory_mb))  # 验证服务通过适配边界请求设备。
        return self.device  # 不访问真实硬件。


class _StubEncoder:
    """返回固定二维向量并记录批量大小。"""

    def __init__(self) -> None:
        """初始化批量大小记录列表。"""
        self.batch_sizes: list[int] = []  # 保存每次编码的批量策略。

    def encode(self, texts: list[str], *, batch_size: int) -> list[list[float]]:
        """返回与输入数量一致的非单位向量，供服务验证归一化。"""
        self.batch_sizes.append(batch_size)  # 记录实际批量大小。
        return [[3.0, 4.0] for _ in texts]  # 返回可验证为单位向量的固定结果。


class _OutOfMemoryOnceEncoder(_StubEncoder):
    """首次编码模拟内存不足，第二次返回固定向量。"""

    def encode(self, texts: list[str], *, batch_size: int) -> list[list[float]]:
        """验证服务仅降低一次批量后重试。"""
        self.batch_sizes.append(batch_size)  # 记录初始和重试批量。
        if len(self.batch_sizes) == 1:  # 仅模拟第一轮资源不足。
            raise BgeM3OutOfMemoryError("内存不足")  # 触发服务的单次降批路径。
        return [[1.0, 0.0] for _ in texts]  # 重试成功后返回有效单位向量。


class _FailingEncoder:
    """模拟模型依赖、权重或设备不可用。"""

    def encode(self, texts: list[str], *, batch_size: int) -> list[list[float]]:
        """始终抛出净化后的适配器错误。"""
        raise BgeM3EncoderError("模型不可用")  # 触发服务稳定错误映射。


def test_encode_documents_normalizes_vectors_and_uses_cpu_batch_size() -> None:
    """文档编码应选择 CPU 批量、归一化向量并记录实际设备。"""
    encoder = _StubEncoder()  # 构造不加载模型的编码器替身。
    resolver = _StubDeviceResolver("cpu")  # 构造不探测硬件的设备替身。
    config = EmbeddingServiceConfig(batch_size_cpu=3, batch_size_gpu=9)  # 使用可区分的 CPU/GPU 批量配置。

    result = asyncio.run(EmbeddingService(config=config, encoder=encoder, device_resolver=resolver).encode_documents(["paper one", "paper two"]))  # 调用公开异步文档编码接口。

    assert result.vectors == ((0.6, 0.8), (0.6, 0.8))  # 验证服务输出单位向量且保持文本顺序。
    assert result.dimension == 2  # 验证维度由返回向量确定。
    assert result.device == "cpu"  # 验证返回实际而非偏好设备。
    assert result.normalized is True  # 验证默认启用余弦检索所需归一化。
    assert encoder.batch_sizes == [3]  # 验证 CPU 使用独立批量策略。
    assert resolver.calls == [("auto", 4096)]  # 验证自动设备策略和显存门槛被传给适配器。


def test_encode_queries_retries_once_with_half_batch_after_oom() -> None:
    """明确 OOM 时应仅降半批量重试一次，避免无限消耗资源。"""
    encoder = _OutOfMemoryOnceEncoder()  # 构造首次 OOM、第二次成功的替身。
    config = EmbeddingServiceConfig(batch_size_cpu=5)  # 使用奇数批量验证向下取整逻辑。

    result = asyncio.run(EmbeddingService(config=config, encoder=encoder, device_resolver=_StubDeviceResolver("cpu")).encode_queries(["query"]))  # 调用公开异步查询编码接口。

    assert encoder.batch_sizes == [5, 2]  # 验证只发生一次减半重试。
    assert result.vectors == ((1.0, 0.0),)  # 验证成功重试结果正常返回。


def test_encode_empty_texts_avoids_model_and_device_loading() -> None:
    """空批次应返回稳定空契约，不探测设备也不调用模型。"""
    encoder = _StubEncoder()  # 构造可记录是否被错误调用的替身。
    resolver = _StubDeviceResolver("cuda")  # 构造可记录是否被错误探测的替身。

    result = asyncio.run(EmbeddingService(encoder=encoder, device_resolver=resolver).encode_documents([]))  # 传入空文档列表。

    assert result.vectors == ()  # 验证返回稳定空向量集合。
    assert result.dimension == 0  # 验证空结果不虚构维度。
    assert result.device == "not_used"  # 验证未使用硬件。
    assert encoder.batch_sizes == []  # 验证不加载或调用模型。
    assert resolver.calls == []  # 验证不探测设备。


def test_encode_maps_model_failure_to_safe_service_error() -> None:
    """模型不可用时服务不得泄露底层路径或设备细节。"""
    service = EmbeddingService(encoder=_FailingEncoder(), device_resolver=_StubDeviceResolver("cpu"))  # 使用可控失败替身创建服务。

    with pytest.raises(EmbeddingServiceError, match="BGE-M3 嵌入模型不可用"):  # 断言稳定的业务层错误。
        asyncio.run(service.encode_documents(["paper"]))  # 触发适配器失败映射。


def test_embedding_config_rejects_invalid_batch_size() -> None:
    """批量大小必须为正，避免将无效配置传入第三方模型。"""
    with pytest.raises(ValueError, match="批量大小必须大于零"):  # 断言配置错误在装配期被发现。
        EmbeddingServiceConfig(batch_size_gpu=0)  # 构造无效 GPU 批量配置。
