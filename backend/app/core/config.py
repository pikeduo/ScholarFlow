"""集中读取 ScholarFlow 的非敏感运行配置。"""

from functools import lru_cache  # 缓存配置实例避免重复解析环境变量。
from pathlib import Path  # 使用跨平台路径表示日志目录。

from pydantic import Field  # 声明配置字段默认值与说明。
from pydantic_settings import BaseSettings, SettingsConfigDict  # 支持环境变量配置模型。


class Settings(BaseSettings):
    """保存可由环境变量覆盖的应用设置。

    环境变量使用 SCHOLARFLOW_ 前缀；数据库凭据等敏感值只应存在于本地 .env 或部署环境。
    """

    model_config = SettingsConfigDict(  # 配置环境变量读取规则。
        env_prefix="SCHOLARFLOW_",  # 防止与其他应用的环境变量冲突。
        env_file=".env",  # 支持用户复制 .env.example 后进行本地配置。
        env_file_encoding="utf-8",  # 确保中文配置说明对应的文件采用 UTF-8。
        extra="ignore",  # 忽略无关环境变量以提升部署兼容性。
    )

    app_name: str = Field(default="ScholarFlow")  # 定义 OpenAPI 与日志显示名称。
    environment: str = Field(default="development")  # 记录当前运行环境名称。
    api_v1_prefix: str = Field(default="/api/v1")  # 集中维护版本化 API 路径。
    database_url: str = Field(default="sqlite:///./data/scholarflow.db")  # 指定开发期 SQLite 地址。
    log_dir: Path = Field(default=Path("logs"))  # 指定可被 Git 忽略的日志目录。
    log_level: str = Field(default="INFO")  # 支持部署时调整日志详细程度。


@lru_cache
def get_settings() -> Settings:
    """创建并缓存一份应用设置。

    返回：
        Settings：经过 Pydantic 校验的配置对象。
    """
    return Settings()  # 在首次使用时解析环境变量和可选 .env 文件。


settings = get_settings()  # 提供给应用模块复用的只读配置实例。
