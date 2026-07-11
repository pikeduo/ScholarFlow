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


def test_settings_allows_optional_semantic_scholar_key_and_preserves_rps() -> None:
    """Semantic Scholar 匿名访问配置应保留 1 RPS 来源级限制。"""
    settings = Settings(  # 构造不读取用户本地 .env 的匿名访问配置。
        _env_file=None,  # 禁止测试读取用户本地配置值。
        semantic_scholar_api_key="   ",  # 提供应被规范化为空值的空白密钥。
        semantic_scholar_requests_per_second=1,  # 提供规划基线要求的 1 RPS。
    )
    assert settings.semantic_scholar_api_key is None  # 验证空白密钥不会被误认为有效认证信息。
    assert settings.semantic_scholar_requests_per_second == 1  # 验证来源级频率配置被正确保存。


def test_settings_rejects_non_positive_semantic_scholar_rps() -> None:
    """来源级 RPS 不能为零或负数，否则无法形成有效节流间隔。"""
    with pytest.raises(ValueError, match="greater than 0"):  # 断言返回数值下界校验错误。
        Settings(_env_file=None, semantic_scholar_requests_per_second=0)  # 构造无效的零请求频率。
