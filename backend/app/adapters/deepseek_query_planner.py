"""封装 DeepSeek 自然语言学术查询规划与 JSON 响应解析。"""

import json  # 序列化不含密钥的用户请求。
from datetime import date  # 向模型提供当前日期以解释“最新”等相对时间。
from time import perf_counter  # 使用单调时钟统计外部查询规划耗时。
from typing import Protocol  # 定义可由测试替换的查询规划边界。

import httpx  # 复用已有异步 HTTP 客户端。
from pydantic import BaseModel, Field, ValidationError, field_validator  # 先规范化供应商常见变体，再严格校验领域输出。

from backend.app.core.config import Settings, settings  # 读取集中 DeepSeek 配置和召回上限。
from backend.app.models.natural_search import NaturalSearchRequest, QueryPlanningResult  # 接收自然语言请求并返回带用量的规划结果。
from backend.app.models.query_intent import PaperType, QueryIntent, QueryLanguage, QuerySubquery  # 构造完整领域契约。


class QueryPlanningError(RuntimeError):
    """表示查询规划配置、网络或结构化输出不可用。"""


class QueryPlanningClient(Protocol):
    """定义自然语言查询规划客户端协议。"""

    async def plan(self, request: NaturalSearchRequest) -> QueryPlanningResult:
        """将自然语言问题转换为带调用统计的可执行计划。"""
        ...


class _PlannedQuery(BaseModel):
    """校验模型可生成的查询规划字段。"""

    normalized_query: str = Field(min_length=1)  # 要求生成适合学术 API 的简洁英文检索式。
    query_language: QueryLanguage  # 标记原始查询语言。
    research_topics: list[str] = Field(default_factory=list)  # 保存英文研究主题。
    methods: list[str] = Field(default_factory=list)  # 保存英文方法名称。
    tasks: list[str] = Field(default_factory=list)  # 保存英文任务名称。
    datasets: list[str] = Field(default_factory=list)  # 保存数据集名称。
    authors: list[str] = Field(default_factory=list)  # 保存作者约束。
    institutions: list[str] = Field(default_factory=list)  # 保存机构约束。
    venues: list[str] = Field(default_factory=list)  # 保存 venue 约束。
    paper_types: list[PaperType] = Field(default_factory=list)  # 保存论文类型约束。
    year_range: tuple[int, int] | None = None  # 保存自然语言中识别的年份范围。
    must_include: list[str] = Field(default_factory=list)  # 保存自然语言中的明确必须条件。
    should_include: list[str] = Field(default_factory=list)  # 保存自然语言中的偏好条件。
    exclude: list[str] = Field(default_factory=list)  # 保存自然语言中的排除条件。
    domains: list[str] = Field(default_factory=list)  # 保存动态来源路由领域。
    complexity_score: float = Field(default=0.0, ge=0.0, le=1.0)  # 保存查询复杂度。
    subqueries: list[QuerySubquery] = Field(default_factory=list)  # 保存最多三条英文子查询。

    @field_validator("paper_types", mode="before")
    @classmethod
    def normalize_paper_types(cls, value: object) -> object:
        """将模型常见论文类型别名映射为核心领域枚举。"""
        if not isinstance(value, list):  # 非列表继续交由 Pydantic 给出严格错误。
            return value  # 保留原值供类型校验。
        aliases = {  # 只接受语义明确且不会改变用户意图的常见别名。
            "article": "article",
            "research article": "article",
            "journal article": "article",
            "journalarticle": "article",
            "conference": "conference",
            "conference paper": "conference",
            "proceedings article": "conference",
            "preprint": "preprint",
            "review": "review",
            "review article": "review",
            "survey": "review",
        }
        normalized_types: list[str] = []  # 保存映射后且去重的稳定枚举。
        for item in value:  # 逐项处理模型输出。
            if not isinstance(item, str):  # 非文本条目由后续严格校验拒绝。
                normalized_types.append(item)  # 保留异常类型以产生可定位错误。
                continue  # 继续检查其余条目。
            normalized_key = " ".join(item.strip().casefold().replace("_", " ").replace("-", " ").split())  # 统一大小写和分隔符。
            mapped_type = aliases.get(normalized_key, normalized_key)  # 已知别名映射，未知值保持并由枚举拒绝。
            if mapped_type not in normalized_types:  # 防止 article 别名产生重复条件。
                normalized_types.append(mapped_type)  # 保留首次规范化类型。
        return normalized_types  # 返回供 Literal 严格校验的列表。

    @field_validator("complexity_score", mode="before")
    @classmethod
    def normalize_complexity_score(cls, value: object) -> object:
        """兼容模型输出的 1–5 复杂度量表并转换为 0–1。"""
        if isinstance(value, bool) or not isinstance(value, (int, float)):  # 布尔值和文本不应被误认为分数。
            return value  # 交由 Pydantic 严格报告类型错误。
        numeric_value = float(value)  # 统一整数和浮点数。
        if 0.0 <= numeric_value <= 1.0:  # 已符合领域契约时保持原值。
            return numeric_value  # 避免重复缩放。
        if 1.0 < numeric_value <= 5.0:  # 兼容常见五级复杂度量表。
            return numeric_value / 5.0  # 例如 3 转换为 0.6。
        return value  # 超出两种已知量表的值继续由范围校验拒绝。

    @field_validator("subqueries", mode="before")
    @classmethod
    def normalize_subquery_languages(cls, value: object) -> object:
        """为按提示生成的英文子查询补齐缺失 language 字段。"""
        if not isinstance(value, list):  # 非列表继续交由核心类型校验。
            return value  # 保留原值。
        normalized_subqueries: list[object] = []  # 保存不修改原始对象的规范化副本。
        for item in value:  # 逐条处理子查询。
            if isinstance(item, dict):  # 只有 JSON 对象可以安全补充字段。
                normalized_item = dict(item)  # 复制以避免修改响应解析对象。
                normalized_item.setdefault("language", "en")  # Query Agent 提示明确要求英文子查询。
                normalized_subqueries.append(normalized_item)  # 写入规范化结果。
            else:  # 非对象条目由后续严格校验拒绝。
                normalized_subqueries.append(item)  # 保留异常值以产生明确错误。
        return normalized_subqueries  # 返回供 QuerySubquery 严格校验的列表。


class DeepSeekQueryPlanningClient:
    """使用 DeepSeek JSON Output 生成结构化学术查询计划。"""

    def __init__(self, config: Settings = settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """保存集中配置和可选离线传输层。"""
        self._config = config  # 延迟到调用时解封装 API Key。
        self._transport = transport  # 允许测试使用 MockTransport。

    async def plan(self, request: NaturalSearchRequest) -> QueryPlanningResult:
        """调用 DeepSeek，并返回显式约束优先的计划及安全用量统计。"""
        try:  # 在外部调用前校验密钥。
            api_key = self._config.require_deepseek_api_key()  # 仅在请求层使用真实密钥。
        except ValueError as exc:  # 缺少密钥时不退回低质量整句搜索。
            raise QueryPlanningError("DeepSeek 查询规划未配置") from exc  # 返回安全错误。
        body = {  # 构造官方 JSON Output 请求。
            "model": self._config.deepseek_model,  # 复用默认 Flash 模型控制成本。
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},  # 约束英文检索式和结构化字段。
                {"role": "user", "content": json.dumps({"current_date": date.today().isoformat(), "query": request.query}, ensure_ascii=False)},  # 只发送查询和当前日期。
            ],
            "response_format": {"type": "json_object"},  # 强制返回 JSON 对象。
            "thinking": {"type": "disabled"},  # 使用低延迟非思考模式。
            "temperature": 0.0,  # 降低同一查询规划波动。
            "max_tokens": 3000,  # 查询规划无需占用精排输出上限。
            "stream": False,  # 等待完整计划后统一校验。
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}  # 密钥仅进入请求头。
        started_at = perf_counter()  # 从实际外部请求前开始统计规划链路耗时。
        try:  # 净化网络、状态和 JSON 解析异常。
            async with httpx.AsyncClient(base_url=self._config.deepseek_api_base_url.rstrip("/"), timeout=self._config.deepseek_timeout_seconds, transport=self._transport) as client:  # 使用集中端点。
                response = await client.post("/chat/completions", headers=headers, json=body)  # 调用聊天完成接口。
                response.raise_for_status()  # 拒绝非成功状态。
                response_data = response.json()  # 在内存解析且不记录正文。
            content = response_data["choices"][0]["message"]["content"]  # 读取首个非流式输出。
            planned = _PlannedQuery.model_validate_json(content)  # 严格校验查询计划。
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:  # 覆盖全部外部边界异常。
            raise QueryPlanningError("DeepSeek 查询规划失败") from exc  # 不泄露响应正文或内部 URL。
        intent = QueryIntent(  # 用模型语义字段和用户显式覆盖构造最终计划。
            original_query=request.query,  # 原始问题始终来自用户。
            normalized_query=planned.normalized_query,  # 使用英文简洁检索式召回。
            query_language=planned.query_language,
            research_topics=planned.research_topics,
            methods=planned.methods,
            tasks=planned.tasks,
            datasets=planned.datasets,
            authors=planned.authors,
            institutions=planned.institutions,
            venues=planned.venues,
            paper_types=planned.paper_types,
            year_range=request.year_range or planned.year_range,  # 用户显式年份优先。
            must_include=request.must_include,  # 只有用户在高级条件中显式填写的词才执行逐字硬过滤，模型语义条件交给后续核验。
            should_include=_merge_terms(planned.should_include, request.should_include),
            exclude=_merge_terms(planned.exclude, request.exclude),
            subqueries=planned.subqueries[:3],  # 限制子查询规模。
            target_paper_count=request.target_paper_count,
            source_recall_count=self._config.academic_source_recall_limit,
            search_mode=request.search_mode,
            domains=_merge_terms(planned.domains, request.domains),
            requires_web_evidence=request.requires_web_evidence,
            complexity_score=planned.complexity_score,
        )
        usage = response_data.get("usage") if isinstance(response_data, dict) else None  # 安全读取供应商可选用量对象。
        usage_data = usage if isinstance(usage, dict) else {}  # 缺少用量时使用零值保持接口稳定。
        model_name = response_data.get("model") if isinstance(response_data, dict) else None  # 读取实际响应模型而非假定配置值。
        duration_ms = max(0, round((perf_counter() - started_at) * 1000))  # 转换为便于日志和前端展示的毫秒数。
        return QueryPlanningResult(  # 将意图和观测数据作为一个原子结果返回服务层。
            query_intent=intent,  # 保存可直接进入多源协调器的计划。
            model_name=model_name if isinstance(model_name, str) else self._config.deepseek_model,  # 响应缺失时回退到配置模型名。
            prompt_tokens=_safe_token_count(usage_data.get("prompt_tokens")),  # 非法或缺失计数安全归零。
            completion_tokens=_safe_token_count(usage_data.get("completion_tokens")),  # 非法或缺失计数安全归零。
            duration_ms=duration_ms,  # 保存完整请求与解析耗时。
        )


_SYSTEM_PROMPT = """你是学术检索 Query Agent。只输出 JSON，不输出 Markdown 或思维过程。将中文或英文问题解析为结构化计划；所有用于学术 API 的主题、方法、任务、数据集、领域和 normalized_query 必须使用规范、简洁的英文术语。不要把“优先”误作硬约束。只有用户明确限定论文类型时才填写 paper_types，不得因为普通“论文”或“研究”措辞推断 article；paper_types 只能使用 article、conference、preprint、review。must_include 只提取用户明确要求逐字包含的术语，方法、任务和数据集分别放入对应字段。complexity_score 必须是 0 到 1。subqueries 最多三条英文查询，每条必须包含 query、language='en' 和 purpose，purpose 只能是 method、dataset、citation。输出字段必须包含 normalized_query、query_language、research_topics、methods、tasks、datasets、authors、institutions、venues、paper_types、year_range、must_include、should_include、exclude、domains、complexity_score、subqueries。"""  # 定义稳定查询规划边界。


def _merge_terms(first: list[str], second: list[str]) -> list[str]:
    """大小写无关合并模型和用户条件并保持顺序。"""
    merged: list[str] = []  # 保存去重后词项。
    seen: set[str] = set()  # 记录规范化词项。
    for term in [*first, *second]:  # 模型结果在前，显式补充在后。
        normalized = term.strip()  # 移除无意义空白。
        key = normalized.casefold()  # 使用大小写无关键。
        if normalized and key not in seen:  # 仅保留首次有效词项。
            merged.append(normalized)  # 保存原始展示形式。
            seen.add(key)  # 标记已出现。
    return merged  # 返回稳定列表。


def _safe_token_count(value: object) -> int:
    """将供应商用量字段转换为非负整数，异常值安全归零。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):  # 排除布尔值、文本和空值。
        return 0  # 保持公共响应中的计数稳定。
    return max(0, int(value))  # 防止异常负数污染成本统计。
