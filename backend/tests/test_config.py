"""验证应用配置对相对日志目录的稳定解析规则。"""

from pathlib import Path  # 构造跨平台路径断言。

from backend.app.core.config import PROJECT_ROOT, Settings  # 导入仓库根目录常量和待测配置模型。


def test_settings_resolves_relative_log_dir_from_project_root() -> None:
    """相对日志目录不应随 pytest 或 IDE 当前工作目录改变。"""
    settings = Settings(_env_file=None, log_dir=Path("temporary-logs"))  # 构造不读取真实 .env 的相对日志目录配置。
    assert settings.log_dir == PROJECT_ROOT / "temporary-logs"  # 验证目录固定锚定到仓库根目录。


def test_settings_preserves_absolute_log_dir() -> None:
    """显式绝对日志目录应保持原样，便于部署环境集中管理日志。"""
    absolute_log_dir = PROJECT_ROOT / "external-logs"  # 构造仓库内的绝对路径作为可移植测试值。
    settings = Settings(_env_file=None, log_dir=absolute_log_dir)  # 构造不读取真实 .env 的绝对日志目录配置。
    assert settings.log_dir == absolute_log_dir  # 验证绝对路径不会被重复拼接或重写。


def test_settings_allows_explicit_fast_path_switches() -> None:
    """模型快速路径开关必须可由部署环境显式关闭。"""
    settings = Settings(_env_file=None, semantic_ranking_enabled=False, cross_encoder_ranking_enabled=False, llm_ranking_enabled=False)  # 构造不读取本地环境的全模型跳过配置。

    assert settings.semantic_ranking_enabled is False  # 验证 BGE-M3 开关保持关闭。
    assert settings.cross_encoder_ranking_enabled is False  # 验证 Cross Encoder 开关保持关闭。
    assert settings.llm_ranking_enabled is False  # 验证 DeepSeek 精排开关保持关闭。


def test_settings_defaults_to_bounded_deepseek_assessment_batches() -> None:
    """论文精排应使用独立短超时、五至十篇批量和受控输出上限。"""
    settings = Settings(_env_file=None)  # 构造不读取用户本地配置的默认设置。

    assert settings.deepseek_timeout_seconds == 60.0  # 验证查询规划仍保留独立的较长超时。
    assert settings.deepseek_llm_timeout_seconds == 30.0  # 验证论文精排不会沿用六十秒长等待。
    assert settings.deepseek_llm_batch_size == 10  # 验证默认批次符合阶段规划的上限。
    assert settings.deepseek_max_output_tokens == 4000  # 验证单批结构化输出不会无界扩大。
