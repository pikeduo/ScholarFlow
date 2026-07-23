"""定义金标、预测、效率和结构化评分共享的离线契约。"""

from typing import Literal  # 限制相关性等级和 JSON 元数据值。

from pydantic import BaseModel, Field, model_validator  # 提供严格字段与跨字段校验。


JsonScalar = str | int | float | bool | None  # 限制元数据为可直接写入 JSON 的标量值。
RelevanceLevel = Literal["high", "partial", "irrelevant", "unknown"]  # 统一预测相关性等级。


class EvaluationPaper(BaseModel):
    """保存可用于匹配和结构化评分的最小论文记录。

    论文允许缺少部分展示字段，以便结构化评分如实反映不完整输出；但至少需要一个
    强标识符、内部标识或标题，避免无身份空对象进入匹配流程。
    """

    paper_id: str | None = None  # 保存 ScholarFlow 内部论文标识，供关系结构引用。
    doi: str | None = None  # 保存可跨来源匹配的 DOI。
    arxiv_id: str | None = None  # 保存可去除版本号后匹配的 arXiv 标识。
    pmid: str | None = None  # 保存 PubMed 标识。
    openalex_id: str | None = None  # 保存 OpenAlex 平台标识。
    semantic_scholar_id: str | None = None  # 保存 Semantic Scholar 平台标识。
    dblp_key: str | None = None  # 保存 DBLP 记录键。
    title: str | None = None  # 保存标题并支持强标识缺失时的保守回退匹配。
    year: int | None = Field(default=None, ge=1000, le=2100)  # 保存发表年份并限制明显异常值。
    authors: list[str] = Field(default_factory=list)  # 保存按原始顺序排列的作者名。
    venue: str | None = None  # 保存期刊或会议名称供结构完整度评分。
    source: str | None = None  # 保存论文事实来源。
    url: str | None = None  # 保存可合法访问的稳定链接。
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)  # 保存归一化相关性分数。
    relevance_level: RelevanceLevel | None = None  # 保存可替代数值分数的相关性等级。
    recommendation_reason: str | None = None  # 保存面向用户的推荐理由。

    @model_validator(mode="after")
    def validate_identity(self) -> "EvaluationPaper":
        """拒绝没有任何可识别信息的空论文对象。

        返回：
            EvaluationPaper：至少包含一个标识或标题的当前记录。
        异常：
            ValueError：所有标识和标题均为空时抛出。
        """
        identity_values = (self.paper_id, self.doi, self.arxiv_id, self.pmid, self.openalex_id, self.semantic_scholar_id, self.dblp_key, self.title)  # 汇总允许建立身份的字段。
        if not any(value and value.strip() for value in identity_values):  # 空白文本不能构成可复核身份。
            raise ValueError("论文至少需要一个标识符、paper_id 或标题")  # 防止空对象污染去重和分母。
        return self  # 返回通过身份边界校验的记录。


class EvaluationUsage(BaseModel):
    """保存在线或离线运行的可选原始效率指标。

    缺失字段始终保持 ``None``，不得用零伪装为已观测值。
    """

    academic_api_calls: int | None = Field(default=None, ge=0)  # 保存学术来源逻辑调用数。
    actual_http_requests: int | None = Field(default=None, ge=0)  # 保存包含重试的实际 HTTP 请求数。
    llm_calls: int | None = Field(default=None, ge=0)  # 保存 Query Agent、策略或精排模型调用总数。
    input_tokens: int | None = Field(default=None, ge=0)  # 保存模型输入 Token 数。
    output_tokens: int | None = Field(default=None, ge=0)  # 保存模型输出 Token 数。
    total_tokens: int | None = Field(default=None, ge=0)  # 保存供应商或调用方记录的 Token 总数。
    latency_ms: float | None = Field(default=None, ge=0)  # 保存端到端耗时。
    retry_count: int | None = Field(default=None, ge=0)  # 保存来源或模型重试次数。
    rate_limit_count: int | None = Field(default=None, ge=0)  # 保存 429 或等价限流次数。
    cache_hit_count: int | None = Field(default=None, ge=0)  # 保存有效缓存命中数。
    bge_input_count: int | None = Field(default=None, ge=0)  # 保存 BGE-M3 输入候选数。
    bge_output_count: int | None = Field(default=None, ge=0)  # 保存 BGE-M3 输出候选数。
    bge_latency_ms: float | None = Field(default=None, ge=0)  # 保存 BGE-M3 本地推理耗时。
    cross_encoder_input_count: int | None = Field(default=None, ge=0)  # 保存 Cross Encoder 输入候选数。
    cross_encoder_output_count: int | None = Field(default=None, ge=0)  # 保存 Cross Encoder 输出候选数。
    cross_encoder_latency_ms: float | None = Field(default=None, ge=0)  # 保存 Cross Encoder 本地推理耗时。
    local_model_device: str | None = None  # 保存实际 CPU 或 GPU 设备说明。
    batch_size: int | None = Field(default=None, ge=1)  # 保存本地模型或 LLM 批量大小。
    oom_retry_count: int | None = Field(default=None, ge=0)  # 保存因显存不足而降批重试的次数。


class RelationRecord(BaseModel):
    """保存预测结果集合内可确定性校验的关系边。"""

    source: str = Field(min_length=1)  # 保存起点论文内部标识。
    target: str = Field(min_length=1)  # 保存终点论文内部标识。
    type: str = Field(min_length=1)  # 保存 cites、same_work 等显式关系类型。


class ClassificationRecord(BaseModel):
    """保存预测结果集合内可确定性校验的论文分类。"""

    paper_id: str = Field(min_length=1)  # 保存被分类论文的内部标识。
    label: str = Field(min_length=1)  # 保存非空分类标签。
