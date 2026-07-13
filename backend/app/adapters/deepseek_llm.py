"""封装 DeepSeek Chat Completions 的论文核验与结构化响应解析。"""

import json  # 构造 UTF-8 友好的提示正文并解析 JSON 模型输出。
from typing import Protocol  # 定义可由测试替身或其他 LLM 供应商实现的边界。

import httpx  # 复用项目已有异步 HTTP 客户端访问 DeepSeek 官方接口。
from pydantic import BaseModel, Field, ValidationError  # 严格校验供应商返回的 JSON 内容。

from backend.app.core.config import Settings, settings  # 从集中配置读取端点、密钥、模型和超时。
from backend.app.models.llm_ranking import LlmAssessmentBatch, LlmPaperAssessment  # 返回供应商无关的结构化核验契约。
from backend.app.models.paper import PaperRecord  # 接收 Cross Encoder 截断后的论文候选。
from backend.app.models.query_intent import QueryIntent  # 接收统一查询意图及其硬软约束。


class LlmAssessmentError(RuntimeError):
    """表示 LLM 配置、网络、响应或结构化输出不可用的已净化错误。"""


class PaperAssessmentClient(Protocol):
    """定义 LLM 论文核验客户端的可替换异步协议。"""

    async def assess(self, query: QueryIntent, papers: list[PaperRecord]) -> LlmAssessmentBatch:
        """核验候选论文并返回逐篇相关性、约束状态、证据和理由。"""
        ...


class _AssessmentPayload(BaseModel):
    """校验 DeepSeek JSON 输出的顶层对象。"""

    assessments: list[LlmPaperAssessment] = Field(default_factory=list)  # 要求模型将逐篇结果放入固定字段。


class DeepSeekPaperAssessmentClient:
    """使用 DeepSeek JSON Output 一次性核验一批候选论文。"""

    def __init__(self, config: Settings = settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """保存集中配置和可选测试传输层，构造阶段不发起网络请求。"""
        self._config = config  # 延迟到实际调用时读取和校验敏感密钥。
        self._transport = transport  # 允许单元测试注入 MockTransport 而不访问网络。

    async def assess(self, query: QueryIntent, papers: list[PaperRecord]) -> LlmAssessmentBatch:
        """调用 DeepSeek Chat Completions 并转换为供应商无关核验结果。

        异常：
            LlmAssessmentError：配置缺失、网络失败、非成功响应或 JSON 结构无效。
        """
        if not papers:  # 空候选不应消耗 API Token。
            return LlmAssessmentBatch(assessments=[], model_name=self._config.deepseek_model)  # 返回稳定空批次。
        try:  # 在请求边界统一净化配置错误，避免向上层暴露敏感配置对象。
            api_key = self._config.require_deepseek_api_key()  # 仅在真实调用前解封装密钥。
        except ValueError as exc:  # 缺少密钥属于可降级的模型配置问题。
            raise LlmAssessmentError("DeepSeek API 未配置") from exc  # 返回不含环境内容的稳定异常。
        request_body = {  # 按官方 Chat Completions JSON Output 契约构造请求。
            "model": self._config.deepseek_model,  # 默认使用成本较低的 Flash 模型。
            "messages": [  # 用系统规则约束证据边界，并在用户消息中提供结构化候选。
                {"role": "system", "content": _SYSTEM_PROMPT},  # 明确要求只输出 JSON 且不得虚构证据。
                {"role": "user", "content": _build_user_prompt(query, papers)},  # 提供查询约束与公开论文元数据。
            ],
            "response_format": {"type": "json_object"},  # 启用官方 JSON Output 模式。
            "thinking": {"type": "disabled"},  # 精排任务使用非思考模式以控制延迟与成本。
            "temperature": 0.0,  # 降低同批候选重复核验时的随机波动。
            "max_tokens": self._config.deepseek_max_output_tokens,  # 防止输出无界增长并为完整 JSON 保留空间。
            "stream": False,  # 当前服务需要完整 JSON 后统一校验。
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}  # 密钥仅进入请求头且不写日志。
        try:  # 将网络与 HTTP 故障统一转换为适配器错误。
            async with httpx.AsyncClient(base_url=str(self._config.deepseek_api_base_url).rstrip("/"), timeout=self._config.deepseek_llm_timeout_seconds, transport=self._transport) as client:  # 仅使用论文核验的小批次超时，避免影响查询规划。
                response = await client.post("/chat/completions", headers=headers, json=request_body)  # 调用 OpenAI 兼容聊天端点。
                response.raise_for_status()  # 非成功响应不得进入业务解析。
                response_data = response.json()  # 仅在内存中解析供应商响应，不记录原文。
        except (httpx.HTTPError, ValueError) as exc:  # 覆盖传输、状态码与外层 JSON 解析失败。
            raise LlmAssessmentError("DeepSeek 论文核验调用失败") from exc  # 隐藏 URL、响应正文和鉴权信息。
        try:  # 对嵌套响应和模型生成内容执行两层结构校验。
            content = response_data["choices"][0]["message"]["content"]  # 读取非流式首个模型输出。
            payload = _AssessmentPayload.model_validate_json(content)  # 验证固定 assessments JSON 对象。
            usage = response_data.get("usage") or {}  # 兼容供应商未返回 Token 统计的情况。
            model_name = str(response_data.get("model") or self._config.deepseek_model)  # 优先记录实际响应模型。
            return LlmAssessmentBatch(  # 转换为供应商无关批次供服务层处理证据和排序。
                assessments=payload.assessments,  # 返回已通过字段范围校验的逐篇结果。
                model_name=model_name,  # 保存实际或配置模型名。
                prompt_tokens=int(usage.get("prompt_tokens") or 0),  # 缺失统计时安全回退为零。
                completion_tokens=int(usage.get("completion_tokens") or 0),  # 缺失统计时安全回退为零。
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:  # 覆盖缺字段、空选择和模型 JSON 不合法。
            raise LlmAssessmentError("DeepSeek 返回了无效的论文核验结果") from exc  # 不泄露可能包含用户查询的响应正文。


_SYSTEM_PROMPT = """你是学术论文结果核验器。必须只输出 JSON 对象，不输出 Markdown 或思维过程。\n
逐篇判断论文与查询的相关性及硬约束是否满足。只能使用输入中的标题、摘要、关键词、作者、年份、venue 和论文类型，不得补充外部知识。\n
evidence 必须是输入论文公开元数据中逐字出现的短片段；证据不足时 constraint_status 使用 uncertain。\n
输出格式：{\"assessments\":[{\"paper_id\":\"原始ID\",\"relevance_score\":0.0,\"constraint_status\":\"satisfied|uncertain|not_satisfied\",\"evidence\":[\"原文片段\"],\"recommendation_reason\":\"简短中文理由\"}]}。"""  # 明确 JSON、证据与状态边界。


def _build_user_prompt(query: QueryIntent, papers: list[PaperRecord]) -> str:
    """将查询约束和论文公开元数据序列化为可复现的 JSON 提示。"""
    query_payload = {  # 只发送核验所需字段，不发送运行状态、密钥或补充网页内容。
        "query": query.normalized_query,  # 使用规范化查询减少无意义差异。
        "topics": query.research_topics,  # 提供研究主题。
        "methods": query.methods,  # 提供目标方法。
        "tasks": query.tasks,  # 提供科研任务。
        "datasets": query.datasets,  # 提供数据集约束。
        "authors": query.authors,  # 提供作者硬约束。
        "institutions": query.institutions,  # 提供机构硬约束。
        "venues": query.venues,  # 提供 venue 硬约束。
        "paper_types": query.paper_types,  # 提供论文类型硬约束。
        "year_range": query.year_range,  # 提供年份闭区间。
        "must_include": query.must_include,  # 提供必须条件。
        "should_include": query.should_include,  # 提供软偏好。
        "exclude": query.exclude,  # 提供排除条件。
    }
    paper_payloads = [  # 为每篇候选建立只含公开元数据的核验对象。
        {
            "paper_id": paper.paper_id,  # 保持模型输出可与输入稳定关联。
            "title": paper.title,  # 提供最强主题证据。
            "abstract": paper.abstract,  # 提供方法、任务和数据集证据。
            "keywords": paper.keywords,  # 补充摘要缺失时的主题证据。
            "authors": [{"name": author.name, "institution": author.institution} for author in paper.authors],  # 提供作者和机构核验字段。
            "year": paper.year,  # 提供确定性年份元数据。
            "venue": paper.venue,  # 提供会议或期刊信息。
            "paper_type": paper.paper_type,  # 提供统一论文类型。
        }
        for paper in papers  # 保持 Cross Encoder 顺序便于模型审阅。
    ]
    return json.dumps({"query_intent": query_payload, "papers": paper_payloads}, ensure_ascii=False, separators=(",", ":"))  # 使用紧凑 UTF-8 JSON 控制输入 Token。
