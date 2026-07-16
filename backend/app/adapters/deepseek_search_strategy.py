"""封装 DeepSeek 覆盖缺口驱动的下一轮检索词策略。"""

import json  # 使用紧凑 JSON 传递受限的查询、缺口和公开论文元数据。
from dataclasses import dataclass  # 返回与供应商无关且便于服务层消费的策略提案。
from typing import Protocol  # 声明可由离线测试替换的策略客户端边界。

import httpx  # 复用项目已有的异步 DeepSeek HTTP 访问边界。
from pydantic import BaseModel, Field, ValidationError  # 严格校验模型 JSON 输出。

from backend.app.core.config import Settings, settings  # 从集中配置读取模型、超时和密钥。
from backend.app.models.coverage import CoverageReport  # 向模型提供已验证的覆盖缺口。
from backend.app.models.paper import PaperRecord  # 仅提供高相关候选的公开元数据摘要。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 复用可直接交给来源适配器的子查询契约。
from backend.app.core.deepseek_pricing import estimate_deepseek_cost_or_zero  # 从无服务聚合副作用的基础模块读取费用估算，避免循环导入。


class SearchStrategyError(RuntimeError):
    """表示 LLM 搜索策略调用不可用时的已净化异常。"""


@dataclass(frozen=True)
class SearchStrategyProposal:
    """保存一次 LLM 策略调用产生的补充子查询和审计统计。"""

    subqueries: list[QuerySubquery]  # 保存模型建议、尚待服务层去重的英文子查询。
    reason: str  # 保存不含原始查询的简短策略理由。
    model_name: str  # 保存实际或配置的模型名称。
    prompt_tokens: int  # 保存供应商返回的输入 Token 数。
    completion_tokens: int  # 保存供应商返回的输出 Token 数。
    estimated_cost_cny: float = 0.0  # 保存本次策略调用基于实际 usage 的人民币估算费用。
    peak_pricing_applied: bool = False  # 标记本次策略调用是否应用工作时间两倍费率。


class SearchStrategyClient(Protocol):
    """定义根据检索证据提出下一轮查询的异步客户端协议。"""

    async def propose(self, query: QueryIntent, coverage_report: CoverageReport, papers: list[PaperRecord], executed_subqueries: list[str]) -> SearchStrategyProposal:
        """返回不放宽硬约束的至多两条英文补充子查询。"""
        ...  # 测试替身不需要触碰真实网络或密钥。


class _StrategyPayload(BaseModel):
    """校验 DeepSeek 返回的固定 JSON 策略对象。"""

    subqueries: list[QuerySubquery] = Field(default_factory=list, max_length=2)  # 限制单次策略只提出两个候选表达。
    reason: str = Field(default="", max_length=500)  # 限制理由长度，避免把长论文摘要带回工作流状态。


class DeepSeekSearchStrategyClient:
    """使用 DeepSeek 根据覆盖缺口和已获论文提出下一轮检索表达。"""

    def __init__(self, config: Settings = settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """保存集中配置与可选离线 HTTP 传输层，不在构造阶段请求模型。"""
        self._config = config  # 延迟到实际策略需要时读取 API 密钥。
        self._transport = transport  # 允许测试注入 MockTransport。

    async def propose(self, query: QueryIntent, coverage_report: CoverageReport, papers: list[PaperRecord], executed_subqueries: list[str]) -> SearchStrategyProposal:
        """调用一次受限 JSON 输出策略，并返回可审计的提案。"""
        try:  # 在请求前统一净化密钥缺失错误。
            api_key = self._config.require_deepseek_api_key()  # 密钥只用于 HTTP Authorization 头。
        except ValueError as exc:  # 本地未配置模型时允许服务层安全回退。
            raise SearchStrategyError("DeepSeek 搜索策略未配置") from exc  # 不泄露环境变量或密钥内容。
        request_body = {  # 构造只含公开元数据和必要控制信息的紧凑请求。
            "model": self._config.deepseek_model,  # 复用低成本 Flash 模型。
            "messages": [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": _build_user_prompt(query, coverage_report, papers, executed_subqueries)}],  # 将固定行为与动态证据分离。
            "response_format": {"type": "json_object"},  # 要求供应商返回可严格解析的 JSON。
            "thinking": {"type": "disabled"},  # 此轻量策略禁止思考链以控制成本与延迟。
            "temperature": 0.0,  # 降低相同覆盖状态下的策略波动。
            "max_tokens": min(1000, self._config.deepseek_max_output_tokens),  # 为每轮额外策略设置独立且较小的输出预算。
            "stream": False,  # 等待完整对象后一次性校验。
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}  # 不将密钥写入日志或状态。
        try:  # 将网络、HTTP 与 JSON 外层异常统一净化。
            async with httpx.AsyncClient(base_url=str(self._config.deepseek_api_base_url).rstrip("/"), timeout=self._config.deepseek_llm_timeout_seconds, transport=self._transport) as client:  # 使用论文核验同级的小批次超时。
                response = await client.post("/chat/completions", headers=headers, json=request_body)  # 调用兼容 Chat Completions 接口。
                response.raise_for_status()  # 非 2xx 响应不能进入业务解析。
                response_data = response.json()  # 仅在内存中读取供应商对象。
            content = response_data["choices"][0]["message"]["content"]  # 读取首个非流式模型结果。
            payload = _StrategyPayload.model_validate_json(content)  # 同时完成 JSON、条数和子查询契约校验。
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:  # 覆盖网络、结构与字段异常。
            raise SearchStrategyError("DeepSeek 搜索策略调用失败") from exc  # 不泄露响应正文、URL 或内部异常。
        usage = response_data.get("usage") if isinstance(response_data, dict) else {}  # 安全读取可选供应商用量。
        usage_data = usage if isinstance(usage, dict) else {}  # 缺失用量时保持稳定零值。
        model_name = response_data.get("model") if isinstance(response_data, dict) else None  # 优先记录实际响应模型。
        resolved_model_name = model_name if isinstance(model_name, str) else self._config.deepseek_model  # 统一实际响应与配置回退后的模型名。
        prompt_tokens = _safe_token_count(usage_data.get("prompt_tokens"))  # 提取供应商报告的完整输入 Token 数。
        completion_tokens = _safe_token_count(usage_data.get("completion_tokens"))  # 提取供应商报告的完整输出 Token 数。
        cost_estimate = estimate_deepseek_cost_or_zero(resolved_model_name, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, prompt_cache_hit_tokens=_safe_token_count(usage_data.get("prompt_cache_hit_tokens")), prompt_cache_miss_tokens=_safe_token_count(usage_data.get("prompt_cache_miss_tokens")))  # 依据当前调用时刻与缓存 usage 固化费用。
        return SearchStrategyProposal(subqueries=payload.subqueries, reason=payload.reason.strip(), model_name=resolved_model_name, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, estimated_cost_cny=cost_estimate.cost_cny, peak_pricing_applied=cost_estimate.peak_pricing_applied)  # 返回供应商无关提案、审计统计与费用。


_SYSTEM_PROMPT = """你是科研论文检索的下一轮策略 Agent。只输出 JSON，不输出 Markdown 或思维过程。根据给定 QueryIntent、已完成的覆盖缺口、已找到论文的公开标题/摘要片段和已执行检索式，生成最多两条简洁英文子查询。不得放宽年份、must_include 或 exclude；不得捏造数据集、论文或引用；不得重复已执行检索式。输出格式：{\"subqueries\":[{\"query\":\"...\",\"language\":\"en\",\"purpose\":\"method|dataset|citation\"}],\"reason\":\"简短中文理由\"}。"""  # 定义严格的事实、约束和输出边界。


def _build_user_prompt(query: QueryIntent, coverage_report: CoverageReport, papers: list[PaperRecord], executed_subqueries: list[str]) -> str:
    """构造不含全文、密钥和个人数据的最小策略上下文。"""
    payload = {  # 只发送能约束下一轮查询且可审计的字段。
        "query": {"normalized_query": query.normalized_query, "topics": query.research_topics, "methods": query.methods, "tasks": query.tasks, "datasets": query.datasets, "year_range": query.year_range, "must_include": query.must_include, "exclude": query.exclude},  # 保留不可放宽边界。
        "coverage_gaps": [{"type": gap.gap_type, "constraint": gap.constraint, "focus": gap.recommended_query_focus} for gap in coverage_report.gaps[:6]],  # 限制缺口数量控制 Token。
        "executed_subqueries": executed_subqueries[-6:],  # 只提供近期执行表达避免重复。
        "papers": [{"title": paper.title, "abstract": paper.abstract[:1200], "keywords": paper.keywords, "year": paper.year} for paper in papers[:8]],  # 最多八篇且截断摘要，不读取 PDF 全文。
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))  # 使用紧凑 UTF-8 JSON 控制输入 Token。


def _safe_token_count(value: object) -> int:
    """将供应商可选 Token 字段转换为稳定的非负整数。"""
    return max(0, int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0  # 非数值或布尔值安全归零。
