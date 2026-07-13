"""定义由查询规划节点产出的完整 QueryIntent 领域契约。"""

from typing import Literal  # 限制查询语言、模式和子查询用途的稳定取值。

from pydantic import BaseModel, Field, model_validator  # 提供结构化查询计划的字段和跨字段校验。


QueryLanguage = Literal["zh", "en", "mixed"]  # 标记原始或子查询使用的自然语言类型。
SearchMode = Literal["standard", "deep"]  # 限制搜索轮次和预算策略的模式类型。
SubqueryPurpose = Literal["method", "dataset", "citation"]  # 标记子查询用于补足的检索目的。
PaperType = Literal["article", "conference", "preprint", "review"]  # 限制用户可指定的论文类型。


class QuerySubquery(BaseModel):
    """描述 QueryIntent 中一条可由来源适配器执行的子查询。

    属性：
        query：面向外部学术来源的具体查询文本。
        language：子查询使用的语言。
        purpose：该子查询补足方法、数据集或引文的目的。
    """

    query: str = Field(min_length=1)  # 确保每条子查询具有可执行的非空文本。
    language: QueryLanguage  # 保存子查询实际使用的语言。
    purpose: SubqueryPurpose  # 保存子查询在检索计划中的明确职责。


class QueryIntent(BaseModel):
    """保存自然语言查询经规划后供所有下游节点消费的统一意图。

    属性：
        original_query：用户输入的原始查询，不写入无授权的公开日志。
        normalized_query：用于缓存键和可复现检索的规范化查询文本。
        query_language：原始查询的语言类型。
        research_topics：研究主题或目标任务关键词。
        methods：方法、模型或算法关键词。
        tasks：待解决的具体科研任务。
        datasets：目标数据集或基准名称。
        authors：作者筛选条件。
        institutions：机构筛选条件。
        venues：期刊或会议筛选条件。
        paper_types：论文类型筛选条件。
        year_range：发表年份闭区间。
        must_include：必须满足的硬约束关键词。
        should_include：尽量满足的软偏好关键词。
        exclude：必须排除的关键词。
        subqueries：可迭代执行的子查询计划。
        target_paper_count：期望返回的最终论文数量。
        source_recall_count：每个学术来源请求的候选数量，未设置时兼容使用最终数量。
        search_mode：标准或深度搜索模式。
        enable_semantic_ranking：是否允许执行 BGE-M3 语义粗排。
        enable_cross_encoder_ranking：是否允许执行 Cross Encoder 重排。
        domains：用于动态选择第三来源的领域标签。
        requires_web_evidence：是否需要补充网页发现证据，默认关闭。
        complexity_score：供模型路由与预算守卫使用的复杂度评分。
    """

    original_query: str = Field(min_length=1)  # 保留用户可编辑和审计的原始查询文本。
    normalized_query: str = Field(min_length=1)  # 保存移除无语义差异后的查询文本。
    query_language: QueryLanguage  # 标记查询语言以支持跨语言检索策略。
    research_topics: list[str] = Field(default_factory=list)  # 保存研究主题或目标任务关键词。
    methods: list[str] = Field(default_factory=list)  # 保存模型、算法和方法关键词。
    tasks: list[str] = Field(default_factory=list)  # 保存具体科研任务关键词。
    datasets: list[str] = Field(default_factory=list)  # 保存数据集或基准筛选条件。
    authors: list[str] = Field(default_factory=list)  # 保存作者筛选条件。
    institutions: list[str] = Field(default_factory=list)  # 保存机构筛选条件。
    venues: list[str] = Field(default_factory=list)  # 保存期刊或会议筛选条件。
    paper_types: list[PaperType] = Field(default_factory=list)  # 保存论文类型筛选条件。
    year_range: tuple[int, int] | None = None  # 允许未指定发表年份范围。
    must_include: list[str] = Field(default_factory=list)  # 保存硬约束关键词。
    should_include: list[str] = Field(default_factory=list)  # 保存软偏好关键词。
    exclude: list[str] = Field(default_factory=list)  # 保存必须排除的关键词。
    subqueries: list[QuerySubquery] = Field(default_factory=list)  # 保存可执行的子查询计划。
    target_paper_count: int = Field(default=20, ge=1, le=100)  # 限制最终结果规模以控制成本。
    source_recall_count: int | None = Field(default=None, ge=1, le=100)  # 将来源召回规模与最终展示数量分离。
    search_mode: SearchMode = "standard"  # 默认使用成本更低的标准检索模式。
    enable_semantic_ranking: bool = False  # 允许用户选择执行 BGE-M3，默认不加载本地模型。
    enable_cross_encoder_ranking: bool = False  # 允许用户选择执行 Cross Encoder，默认不加载本地模型。
    domains: list[str] = Field(default_factory=list)  # 保存用于动态来源路由的领域标签。
    requires_web_evidence: bool = False  # 仅在需要网页补充证据时允许 Tavily 进入路由计划。
    complexity_score: float = Field(default=0.0, ge=0.0, le=1.0)  # 限制模型路由评分为闭区间。

    @model_validator(mode="after")
    def validate_constraints(self) -> "QueryIntent":
        """校验年份范围与硬约束、软偏好、排除词之间的冲突。

        返回：
            QueryIntent：通过校验的当前查询意图。
        异常：
            ValueError：年份倒置或关键词条件冲突时抛出。
        """
        if self.year_range and self.year_range[0] > self.year_range[1]:  # 防止产生无法执行的倒置年份区间。
            raise ValueError("year_range 的起始年份不能晚于结束年份")  # 返回可供 API 层展示的稳定错误。
        if self.source_recall_count is not None and self.source_recall_count < self.target_paper_count:  # 来源候选不应少于最终目标。
            raise ValueError("source_recall_count 不能小于 target_paper_count")  # 防止配置主动压缩召回。
        must_terms = {term.strip().casefold() for term in self.must_include if term.strip()}  # 规范化硬约束用于冲突比较。
        should_terms = {term.strip().casefold() for term in self.should_include if term.strip()}  # 规范化软偏好用于冲突比较。
        excluded_terms = {term.strip().casefold() for term in self.exclude if term.strip()}  # 规范化排除词用于冲突比较。
        if must_terms & excluded_terms:  # 硬约束与排除条件不能同时要求同一词。
            raise ValueError("must_include 与 exclude 不能包含相同关键词")  # 避免生成不可解释的搜索计划。
        if should_terms & excluded_terms:  # 软偏好与排除条件也不能指向同一词。
            raise ValueError("should_include 与 exclude 不能包含相同关键词")  # 避免排序阶段出现相互矛盾的偏好。
        return self  # 返回通过全部跨字段约束的查询意图。
