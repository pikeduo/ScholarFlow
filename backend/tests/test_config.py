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
