"""验证 OpenAlex 配置的默认值和密钥校验行为。"""

import pytest  # 提供异常断言工具。

from backend.app.core.config import Settings  # 导入待测配置模型。


def test_settings_accepts_openalex_connection_configuration() -> None:
    """有效的 OpenAlex 密钥和超时值应被配置模型保留。"""
    settings = Settings(  # 使用显式构造参数隔离本地环境变量影响。
        _env_file=None,  # 禁止测试读取用户本地 .env 文件。
        openalex_api_key="test-api-key",  # 提供不具备真实权限的测试密钥。
        openalex_timeout_seconds=15,  # 提供有效超时值。
    )
    assert settings.openalex_api_base_url == "https://api.openalex.org"  # 验证默认 API 地址。
    assert settings.require_openalex_api_key() == "test-api-key"  # 验证有效密钥可供适配器按需读取。
    assert settings.openalex_timeout_seconds == 15  # 验证超时配置被正确保存。


def test_settings_rejects_missing_openalex_api_key_when_required() -> None:
    """适配器请求前应拒绝未配置或空白的 OpenAlex 密钥。"""
    settings = Settings(_env_file=None, openalex_api_key="   ")  # 构造仅包含空白密钥的配置。
    with pytest.raises(ValueError, match="SCHOLARFLOW_OPENALEX_API_KEY"):  # 断言错误明确指出缺失环境变量。
        settings.require_openalex_api_key()  # 模拟未来适配器在请求前读取密钥。
