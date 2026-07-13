"""验证本地 BGE-M3 与 Cross Encoder 首次加载日志和实例复用。"""

import sys  # 临时注入不下载模型的 FlagEmbedding 测试模块。
from types import ModuleType  # 构造最小可导入模块替身。
from unittest.mock import patch  # 验证模型加载阶段日志而不写入测试控制台。

import pytest  # 验证已知依赖不兼容时返回稳定错误。

from backend.app.adapters.bge_m3 import BgeM3Encoder  # 导入待测 BGE-M3 懒加载适配器。
from backend.app.adapters.cross_encoder import BgeCrossEncoder, CrossEncoderError  # 导入待测 Cross Encoder 懒加载适配器和兼容性错误。


class _FakeBgeModel:
    """替代真实 BGE-M3 权重加载的空模型。"""


class _FakeReranker:
    """替代真实 Cross Encoder 权重加载的空模型。"""


class _FixedDeviceResolver:
    """为模型加载测试返回固定设备，避免依赖测试机实际 CUDA 状态。"""

    def __init__(self, device: str) -> None:
        """保存应返回给本地模型适配器的稳定设备名称。"""
        self._device = device  # 保存 cpu 或 cuda 测试设备。

    def resolve(self, _: str, __: int) -> str:
        """返回预设设备，模拟已通过显存门槛的硬件探测结果。"""
        return self._device  # 不导入 torch 或访问真实 GPU。


def _fake_flag_embedding_module() -> ModuleType:
    """构造同时提供两种模型构造器的离线 FlagEmbedding 模块。"""
    module = ModuleType("FlagEmbedding")  # 使用生产导入名称创建模块。
    module.BGEM3FlagModel = lambda _model_name, use_fp16=False, device=None: _FakeBgeModel()  # 接收设备参数并返回不下载权重的 BGE 替身。
    module.FlagReranker = lambda _model_name, use_fp16=False, devices=None: _FakeReranker()  # 接收显式设备参数并返回不下载权重的重排替身。
    return module  # 返回可注入 sys.modules 的模块对象。


def test_bge_m3_logs_first_load_and_reuses_instance() -> None:
    """BGE-M3 应记录首次加载起止信息并在后续调用复用模型。"""
    encoder = BgeM3Encoder()  # 构造尚未加载模型的适配器。
    with patch.dict(sys.modules, {"FlagEmbedding": _fake_flag_embedding_module()}), patch("backend.app.adapters.bge_m3.logger.info") as log_info:  # 隔离依赖和日志输出。
        first_model = encoder._get_model()  # 首次调用触发离线模型构造。
        second_model = encoder._get_model()  # 第二次调用应直接复用实例。

    assert first_model is second_model  # 验证同一适配器不重复初始化模型。
    assert log_info.call_count == 2  # 验证仅记录一次开始和一次完成。
    assert "开始加载" in log_info.call_args_list[0].args[0]  # 验证首次日志可定位下载阶段。
    assert "加载完成" in log_info.call_args_list[1].args[0]  # 验证完成日志可定位加载耗时。


def test_cross_encoder_logs_first_load_and_reuses_instance() -> None:
    """Cross Encoder 应记录首次加载起止信息并在后续调用复用模型。"""
    encoder = BgeCrossEncoder()  # 构造尚未加载模型的适配器。
    with patch.dict(sys.modules, {"FlagEmbedding": _fake_flag_embedding_module()}), patch("backend.app.adapters.cross_encoder.distribution_version", return_value="4.57.3"), patch("backend.app.adapters.cross_encoder.logger.info") as log_info:  # 隔离依赖、兼容版本元数据和日志输出。
        first_model = encoder._get_model()  # 首次调用触发离线模型构造。
        second_model = encoder._get_model()  # 第二次调用应直接复用实例。

    assert first_model is second_model  # 验证同一适配器不重复初始化模型。
    assert log_info.call_count == 2  # 验证仅记录一次开始和一次完成。
    assert "开始加载" in log_info.call_args_list[0].args[0]  # 验证首次日志标记下载或磁盘加载阶段。
    assert "加载完成" in log_info.call_args_list[1].args[0]  # 验证完成日志标记加载耗时。


def test_cross_encoder_rejects_transformers_five_before_loading_model() -> None:
    """FlagEmbedding 1.4.x 遇到 Transformers 5 时应在权重加载前返回可操作错误。"""
    encoder = BgeCrossEncoder()  # 构造尚未加载模型的 Cross Encoder 适配器。
    with patch("backend.app.adapters.cross_encoder.distribution_version", return_value="5.13.1"), patch("backend.app.adapters.cross_encoder.logger.exception") as log_exception:  # 模拟日志中出现的已安装不兼容版本并隔离错误日志。
        with pytest.raises(CrossEncoderError, match="requirements.txt"):
            encoder._get_model()  # 验证不会继续导入或加载 FlagEmbedding 权重。

    assert log_exception.call_count == 1  # 验证不兼容组合被记录为明确的依赖问题。


def test_local_rankers_pass_resolved_cuda_and_enable_fp16_to_flag_embedding() -> None:
    """解析到 CUDA 时，两个本地排序模型都应显式使用 CUDA 并启用半精度。"""
    calls: list[tuple[str, bool, str | None]] = []  # 保存两个 FlagEmbedding 构造器收到的设备与精度参数。
    module = _fake_flag_embedding_module()  # 构造不会下载权重的替身模块。
    module.BGEM3FlagModel = lambda _model_name, use_fp16=False, device=None: calls.append(("bge", use_fp16, device)) or _FakeBgeModel()  # 记录 BGE-M3 的 CUDA 装配参数。
    module.FlagReranker = lambda _model_name, use_fp16=False, devices=None: calls.append(("cross", use_fp16, devices)) or _FakeReranker()  # 记录 Cross Encoder 的 CUDA 装配参数。
    resolver = _FixedDeviceResolver("cuda")  # 注入稳定 CUDA 解析结果而不要求测试机存在显卡。

    with patch.dict(sys.modules, {"FlagEmbedding": module}), patch("backend.app.adapters.cross_encoder.distribution_version", return_value="4.57.3"):  # 隔离模型模块与依赖版本元数据。
        BgeM3Encoder(device_preference="cuda", device_resolver=resolver)._get_model()  # 触发 BGE-M3 的 CUDA 懒加载。
        BgeCrossEncoder(device_preference="cuda", device_resolver=resolver)._get_model()  # 触发 Cross Encoder 的 CUDA 懒加载。

    assert calls == [("bge", True, "cuda"), ("cross", True, "cuda")]  # 验证两个模型均收到相同 CUDA 设备和半精度策略。
