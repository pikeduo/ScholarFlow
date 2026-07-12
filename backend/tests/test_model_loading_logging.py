"""验证本地 BGE-M3 与 Cross Encoder 首次加载日志和实例复用。"""

import sys  # 临时注入不下载模型的 FlagEmbedding 测试模块。
from types import ModuleType  # 构造最小可导入模块替身。
from unittest.mock import patch  # 验证模型加载阶段日志而不写入测试控制台。

from backend.app.adapters.bge_m3 import BgeM3Encoder  # 导入待测 BGE-M3 懒加载适配器。
from backend.app.adapters.cross_encoder import BgeCrossEncoder  # 导入待测 Cross Encoder 懒加载适配器。


class _FakeBgeModel:
    """替代真实 BGE-M3 权重加载的空模型。"""


class _FakeReranker:
    """替代真实 Cross Encoder 权重加载的空模型。"""


def _fake_flag_embedding_module() -> ModuleType:
    """构造同时提供两种模型构造器的离线 FlagEmbedding 模块。"""
    module = ModuleType("FlagEmbedding")  # 使用生产导入名称创建模块。
    module.BGEM3FlagModel = lambda _model_name, use_fp16=False, device=None: _FakeBgeModel()  # 接收设备参数并返回不下载权重的 BGE 替身。
    module.FlagReranker = lambda _model_name, use_fp16=False: _FakeReranker()  # 返回不下载权重的重排替身。
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
    with patch.dict(sys.modules, {"FlagEmbedding": _fake_flag_embedding_module()}), patch("backend.app.adapters.cross_encoder.logger.info") as log_info:  # 隔离依赖和日志输出。
        first_model = encoder._get_model()  # 首次调用触发离线模型构造。
        second_model = encoder._get_model()  # 第二次调用应直接复用实例。

    assert first_model is second_model  # 验证同一适配器不重复初始化模型。
    assert log_info.call_count == 2  # 验证仅记录一次开始和一次完成。
    assert "开始加载" in log_info.call_args_list[0].args[0]  # 验证首次日志标记下载或磁盘加载阶段。
    assert "加载完成" in log_info.call_args_list[1].args[0]  # 验证完成日志标记加载耗时。
