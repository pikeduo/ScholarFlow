"""集中读取 ScholarFlow 的非敏感运行配置。"""

from functools import lru_cache  # 缓存配置实例避免重复解析环境变量。
from pathlib import Path  # 使用跨平台路径表示日志目录。
from typing import Literal  # 限制本地模型设备策略的公开取值。

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
    redis_enabled: bool = Field(default=False)  # 控制 Redis 短期存储是否在当前环境启用，默认保持 SQLite 单机可用。
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", min_length=1)  # 默认使用 Windows IPv4-only Redis 可达的本地回环地址。
    redis_key_prefix: str = Field(default="ScholarFlow", pattern=r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$")  # 以统一项目名前缀隔离 DB 0 中的 ScholarFlow 键空间。
    redis_socket_timeout_seconds: float = Field(default=2.0, gt=0, le=30)  # 限制 Redis 不可用时单次连接或命令等待时间。
    redis_source_search_cache_ttl_seconds: int = Field(default=14400, ge=60, le=86400)  # 限制学术来源搜索响应最多缓存四小时，避免长期保留过时结果。
    academic_api_max_retries: int = Field(default=3, ge=0, le=5)  # 所有幂等学术 API 在首次请求后允许的统一最大重试次数。
    academic_api_backoff_initial_seconds: float = Field(default=15.0, gt=0, le=300)  # 未收到有效 Retry-After 时的首次指数退避等待。
    academic_api_backoff_max_seconds: float = Field(default=60.0, gt=0, le=600)  # 限制指数退避避免临时故障造成过长等待。
    academic_api_jitter_max_seconds: float = Field(default=3.0, ge=0, le=60)  # 每次重试额外加入的最大随机抖动。
    academic_api_cooldown_seconds: float = Field(default=60.0, ge=30, le=3600)  # 最终 429 后所有学术来源的默认冷却时间。
    academic_api_retry_after_max_seconds: float = Field(default=300.0, ge=30, le=3600)  # 限制供应商 Retry-After 防止异常响应无限阻塞。
    openalex_api_base_url: str = Field(default="https://api.openalex.org", pattern=r"^https://")  # 限制 OpenAlex 使用 HTTPS 地址。
    openalex_api_key: SecretStr | None = None  # 保存不可写入日志的 OpenAlex API 密钥。
    openalex_timeout_seconds: float = Field(default=10.0, gt=0, le=120)  # 限制未来适配器的单次请求等待时间。
    openalex_requests_per_second: float = Field(default=5.0, gt=0, le=20)  # 为 OpenAlex 保留独立且可配置的来源级 RPS。
    arxiv_api_base_url: str = Field(default="https://export.arxiv.org/api", pattern=r"^https://")  # 限制 arXiv 使用 HTTPS API 地址。
    arxiv_timeout_seconds: float = Field(default=15.0, gt=0, le=120)  # 为 Atom XML 响应保留略长的单次请求超时。
    arxiv_requests_per_second: float = Field(default=1 / 3, gt=0, le=1)  # 默认遵守官方建议的连续请求至少间隔三秒。
    pubmed_api_base_url: str = Field(default="https://eutils.ncbi.nlm.nih.gov/entrez/eutils", pattern=r"^https://")  # 限制 PubMed E-utilities 使用 HTTPS 地址。
    pubmed_timeout_seconds: float = Field(default=15.0, gt=0, le=120)  # 为 PubMed XML 元数据响应保留合理的单次请求超时。
    pubmed_requests_per_second: float = Field(default=3.0, gt=0, le=10)  # 未配置 NCBI API Key 时遵守每秒最多三次请求的保守限制。
    pubmed_tool: str = Field(default="ScholarFlow", min_length=1, max_length=100)  # 向 NCBI 标识调用应用，便于来源侧识别合规流量。
    pubmed_email: str | None = Field(default=None, max_length=254)  # 可选的 NCBI 联系邮箱，仅在配置后随请求发送。
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
    semantic_scholar_enabled: bool = False  # 必须由用户在密钥获批后显式启用，默认不进入动态路由。
    semantic_scholar_timeout_seconds: float = Field(default=10.0, gt=0, le=120)  # 限制 Semantic Scholar 单次请求等待时间。
    semantic_scholar_requests_per_second: float = Field(default=1.0, gt=0, le=10)  # 配置来源级请求起始频率上限。
    deepseek_api_base_url: str = Field(default="https://api.deepseek.com", pattern=r"^https://")  # 限制 DeepSeek 使用官方或兼容的 HTTPS 端点。
    deepseek_api_key: SecretStr | None = None  # 保存不可写入日志或 API 响应的 DeepSeek 密钥。
    deepseek_model: str = Field(default="deepseek-v4-flash", min_length=1)  # 默认使用规划指定的低成本 Flash 模型。
    deepseek_timeout_seconds: float = Field(default=60.0, gt=0, le=300)  # 为查询规划等非论文精排的 DeepSeek 请求保留响应时间。
    deepseek_llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)  # 限制单个论文核验小批次的等待时间，避免上游慢响应长期阻塞搜索。
    deepseek_llm_batch_size: int = Field(default=10, ge=5, le=10)  # 将论文核验固定限制为规划要求的每批五至十篇。
    deepseek_max_output_tokens: int = Field(default=4000, ge=1000, le=50000)  # 限制单个核验小批次的结构化输出规模，避免无界输出拖慢响应。
    semantic_ranking_enabled: bool = Field(default=True)  # 允许在快速路径跳过本地 BGE-M3 粗排并沿用 RRF 顺序。
    cross_encoder_ranking_enabled: bool = Field(default=True)  # 允许在快速路径跳过本地 Cross Encoder 重排并沿用已有排序。
    local_model_device: Literal["auto", "cpu", "cuda"] = Field(default="auto")  # 统一指定 BGE-M3 与 Cross Encoder 的设备；自动模式优先使用显存足够的 CUDA。
    local_model_minimum_cuda_memory_mb: int = Field(default=4096, ge=1)  # 自动模式启用 CUDA 所需的最小总显存，避免低显存设备频繁 OOM。
    llm_ranking_enabled: bool = Field(default=True)  # 允许在快速路径跳过 DeepSeek 论文核验与理由生成。
    academic_source_recall_limit: int = Field(default=50, ge=20, le=100)  # 自然语言搜索时每个学术来源的候选召回上限。
    llm_minimum_relevance_score: float = Field(default=0.35, ge=0.0, le=1.0)  # 最终结果最低 LLM 相关度，低于 0.35 的弱相关论文不得透传。

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

    @field_validator("deepseek_api_key", mode="before")
    @classmethod
    def normalize_deepseek_api_key(cls, value: object) -> object:
        """将空白 DeepSeek API 密钥统一视为未配置。

        参数：
            value：环境变量或构造参数提供的原始密钥值。
        返回：
            object：规范化后的密钥值或空值。
        """
        if isinstance(value, str) and not value.strip():  # 避免将空字符串误认为有效 Bearer 认证信息。
            return None  # 让 LLM 适配器在调用前返回稳定配置错误。
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

    def require_deepseek_api_key(self) -> str:
        """返回已配置的 DeepSeek API 密钥。

        返回：
            str：仅供 LLM 适配器 Authorization 请求头使用的密钥文本。
        异常：
            ValueError：尚未配置密钥时抛出，避免发出必然失败的请求。
        """
        if self.deepseek_api_key is None:  # 在外部模型调用前提前发现缺失配置。
            raise ValueError("未配置 SCHOLARFLOW_DEEPSEEK_API_KEY")  # 提供不泄露敏感值的明确错误。
        return self.deepseek_api_key.get_secret_value()  # 仅在适配器真正发请求时解封装密钥。


@lru_cache
def get_settings() -> Settings:
    """创建并缓存一份应用设置。

    返回：
        Settings：经过 Pydantic 校验的配置对象。
    """
    return Settings()  # 在首次使用时解析环境变量和可选 .env 文件。


settings = get_settings()  # 提供给应用模块复用的只读配置实例。
