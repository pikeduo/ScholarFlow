"""集中读取 ScholarFlow 的非敏感运行配置。"""

from functools import lru_cache  # 缓存配置实例避免重复解析环境变量。
from pathlib import Path  # 使用跨平台路径表示日志目录。

from pydantic import Field, SecretStr, field_validator, model_validator  # 声明配置字段、敏感值与字段校验。
from pydantic_settings import BaseSettings, SettingsConfigDict  # 支持环境变量配置模型。


PROJECT_ROOT = Path(__file__).resolve().parents[3]  # 根据当前配置模块位置定位仓库根目录。


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
    openalex_api_base_url: str = Field(default="https://api.openalex.org", pattern=r"^https://")  # 限制 OpenAlex 使用 HTTPS 地址。
    openalex_api_key: SecretStr | None = None  # 保存不可写入日志的 OpenAlex API 密钥。
    openalex_timeout_seconds: float = Field(default=10.0, gt=0, le=120)  # 限制未来适配器的单次请求等待时间。
    arxiv_api_base_url: str = Field(default="https://export.arxiv.org/api", pattern=r"^https://")  # 限制 arXiv 使用 HTTPS API 地址。
    arxiv_timeout_seconds: float = Field(default=15.0, gt=0, le=120)  # 为 Atom XML 响应保留略长的单次请求超时。
    arxiv_requests_per_second: float = Field(default=1 / 3, gt=0, le=1)  # 默认遵守官方建议的连续请求至少间隔三秒。
    dblp_api_base_url: str = Field(default="https://dblp.org/search/publ", pattern=r"^https://")  # 限制 DBLP 使用 HTTPS 出版物搜索地址。
    dblp_timeout_seconds: float = Field(default=10.0, gt=0, le=120)  # 限制 DBLP 单次请求等待时间。
    dblp_requests_per_second: float = Field(default=1.0, gt=0, le=5)  # 设置保守的 DBLP 来源级请求频率上限。
    tavily_api_base_url: str = Field(default="https://api.tavily.com", pattern=r"^https://")  # 限制 Tavily 使用 HTTPS API 地址。
    tavily_api_key: SecretStr | None = None  # 保存不可写入日志的 Tavily API 密钥。
    tavily_timeout_seconds: float = Field(default=10.0, gt=0, le=120)  # 限制 Tavily 单次请求等待时间。
    tavily_requests_per_second: float = Field(default=1.0, gt=0, le=5)  # 配置 Tavily 来源级请求频率上限。
    tavily_max_results: int = Field(default=5, ge=1, le=20)  # 限制补充发现数量避免挤占主论文来源预算。
    semantic_scholar_api_base_url: str = Field(default="https://api.semanticscholar.org/graph/v1", pattern=r"^https://")  # 限制 Semantic Scholar 使用 HTTPS Graph API 地址。
    semantic_scholar_api_key: SecretStr | None = None  # 保存可选且不可写入日志的 Semantic Scholar API 密钥。
    semantic_scholar_timeout_seconds: float = Field(default=10.0, gt=0, le=120)  # 限制 Semantic Scholar 单次请求等待时间。
    semantic_scholar_requests_per_second: float = Field(default=1.0, gt=0, le=10)  # 配置来源级请求起始频率上限。

    @model_validator(mode="after")
    def resolve_project_relative_paths(self) -> "Settings":
        """将相对日志目录稳定解析到仓库根目录。

        返回：
            Settings：日志目录已转换为绝对路径的当前配置实例。
        """
        if not self.log_dir.is_absolute():  # 仅转换默认值或环境变量提供的相对目录。
            self.log_dir = (PROJECT_ROOT / self.log_dir).resolve()  # 避免 pytest 或 IDE 工作目录改变日志位置。
        return self  # 保留用户显式提供的绝对日志目录。

    @field_validator("openalex_api_key", mode="before")
    @classmethod
    def normalize_openalex_api_key(cls, value: object) -> object:
        """将空白 API 密钥统一视为未配置。

        参数：
            value：环境变量或构造参数提供的原始密钥值。
        返回：
            object：规范化后的密钥值或空值。
        """
        if isinstance(value, str) and not value.strip():  # 避免将空字符串误认为有效密钥。
            return None  # 让缺失密钥在实际调用前得到明确提示。
        return value  # 保留由 Pydantic 转换为 SecretStr 的有效密钥。

    @field_validator("semantic_scholar_api_key", mode="before")
    @classmethod
    def normalize_semantic_scholar_api_key(cls, value: object) -> object:
        """将空白 Semantic Scholar API 密钥统一视为未配置。

        参数：
            value：环境变量或构造参数提供的原始密钥值。
        返回：
            object：规范化后的密钥值或空值。
        """
        if isinstance(value, str) and not value.strip():  # 避免将空字符串误认为有效认证信息。
            return None  # 允许客户端按官方匿名访问策略决定是否携带请求头。
        return value  # 保留由 Pydantic 转换为 SecretStr 的有效密钥。

    @field_validator("tavily_api_key", mode="before")
    @classmethod
    def normalize_tavily_api_key(cls, value: object) -> object:
        """将空白 Tavily API 密钥统一视为未配置。

        参数：
            value：环境变量或构造参数提供的原始密钥值。
        返回：
            object：规范化后的密钥值或空值。
        """
        if isinstance(value, str) and not value.strip():  # 避免将空字符串误认为可用于 Bearer 认证。
            return None  # 让调用前配置校验给出稳定提示。
        return value  # 保留由 Pydantic 转换为 SecretStr 的有效密钥。

    def require_openalex_api_key(self) -> str:
        """返回已配置的 OpenAlex API 密钥。

        返回：
            str：仅供 HTTP 适配器请求头或参数使用的密钥文本。
        异常：
            ValueError：尚未配置密钥时抛出，避免发出必然失败的请求。
        """
        if self.openalex_api_key is None:  # 在网络请求前提前发现缺失配置。
            raise ValueError("未配置 SCHOLARFLOW_OPENALEX_API_KEY")  # 提供不泄露敏感值的明确错误。
        return self.openalex_api_key.get_secret_value()  # 仅在调用方真正需要时解封装密钥。

    def require_tavily_api_key(self) -> str:
        """返回已配置的 Tavily API 密钥。

        返回：
            str：仅供 Tavily HTTP Authorization 请求头使用的密钥文本。
        异常：
            ValueError：尚未配置密钥时抛出，避免发出必然失败的请求。
        """
        if self.tavily_api_key is None:  # 在网络请求前提前发现缺失配置。
            raise ValueError("未配置 SCHOLARFLOW_TAVILY_API_KEY")  # 提供不泄露敏感值的明确错误。
        return self.tavily_api_key.get_secret_value()  # 仅在实际认证请求层解封装密钥。


@lru_cache
def get_settings() -> Settings:
    """创建并缓存一份应用设置。

    返回：
        Settings：经过 Pydantic 校验的配置对象。
    """
    return Settings()  # 在首次使用时解析环境变量和可选 .env 文件。


settings = get_settings()  # 提供给应用模块复用的只读配置实例。
