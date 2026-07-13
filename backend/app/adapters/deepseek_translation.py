"""封装 DeepSeek 的论文标题与摘要简体中文翻译调用。"""

import json  # 以 UTF-8 JSON 向模型传递结构化论文文本。
from typing import Literal, Protocol  # 定义可替换的翻译适配器协议和字段范围。

import httpx  # 复用项目统一的异步 HTTP 客户端。
from pydantic import BaseModel, Field, ValidationError  # 严格校验模型返回的 JSON 翻译对象。

from backend.app.core.config import Settings, settings  # 从集中配置读取 DeepSeek 端点、模型和密钥。
from backend.app.models.paper import PaperRecord  # 只接受已保存的规范化论文事实。
from backend.app.models.paper_translation import PaperTranslationResponse  # 返回稳定的中文翻译契约。


class PaperTranslationError(RuntimeError):
    """表示翻译配置、网络或模型输出不可用的安全领域错误。"""


class PaperTranslationClient(Protocol):
    """定义按需翻译已保存论文的可替换异步边界。"""

    async def translate(self, paper: PaperRecord, field: Literal["title", "abstract"]) -> PaperTranslationResponse:
        """将论文指定的标题或摘要字段翻译为简体中文。"""
        ...


class _TranslationPayload(BaseModel):
    """校验 DeepSeek 必须返回的最小 JSON 翻译内容。"""

    text_zh: str = Field(min_length=1, max_length=50000)  # 保存单个标题或摘要字段的简体中文译文。


class DeepSeekPaperTranslationClient:
    """调用 DeepSeek，将一篇已保存论文的标题和摘要翻译为简体中文。"""

    def __init__(self, config: Settings = settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """保存集中配置和可选离线传输层，构造阶段不访问网络。"""
        self._config = config  # 延迟到真实翻译请求前读取敏感配置。
        self._transport = transport  # 允许单测注入 MockTransport。

    async def translate(self, paper: PaperRecord, field: Literal["title", "abstract"]) -> PaperTranslationResponse:
        """调用 DeepSeek 并返回标题或摘要字段的简体中文翻译。

        异常：
            PaperTranslationError：密钥、网络、状态码或模型输出不符合契约时抛出。
        """
        source_text = paper.title if field == "title" else paper.abstract  # 只提取用户请求的单一公开文本字段。
        if not source_text.strip():  # 缺失字段不应消耗模型调用。
            raise PaperTranslationError("论文摘要暂缺，无法翻译")  # 返回可直接展示的明确公共错误。
        try:  # 在网络请求前校验密钥配置。
            api_key = self._config.require_deepseek_api_key()  # 仅在适配器请求层解封装密钥。
        except ValueError as exc:  # 缺失配置不应泄露环境字段或原始异常。
            raise PaperTranslationError("DeepSeek 翻译未配置") from exc  # 映射为稳定公共错误。
        body = {  # 构造不含用户身份信息的 JSON Output 翻译请求。
            "model": self._config.deepseek_model,  # 复用项目已配置的低成本默认模型。
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},  # 强制忠实翻译与固定 JSON 响应。
                {"role": "user", "content": json.dumps({"field": field, "text": source_text}, ensure_ascii=False)},  # 仅发送用户请求的已保存公开字段。
            ],
            "response_format": {"type": "json_object"},  # 要求模型返回可严格校验的对象。
            "temperature": 0,  # 降低术语翻译的随机性。
            "max_tokens": self._config.deepseek_max_output_tokens,  # 遵守项目统一输出预算。
            "stream": False,  # 等待完整翻译后再返回稳定响应。
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}  # 密钥只进入 HTTPS 请求头。
        try:  # 统一隔离网络、状态码、JSON 和字段结构错误。
            async with httpx.AsyncClient(base_url=self._config.deepseek_api_base_url.rstrip("/"), timeout=self._config.deepseek_timeout_seconds, transport=self._transport) as client:  # 使用集中端点和超时。
                response = await client.post("/chat/completions", headers=headers, json=body)  # 调用兼容 Chat Completions 的翻译能力。
                response.raise_for_status()  # 非成功状态不能被误当成翻译结果。
                response_data = response.json()  # 仅在内存解析响应正文。
            content = response_data["choices"][0]["message"]["content"]  # 提取首个完整模型输出。
            translated = _TranslationPayload.model_validate_json(content)  # 严格验证模型返回的两个译文字段。
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:  # 覆盖供应商与解析层全部可预期边界。
            raise PaperTranslationError("DeepSeek 论文翻译失败，请稍后重试") from exc  # 不泄露端点、响应正文或调用细节。
        model_name = response_data.get("model") if isinstance(response_data, dict) else None  # 优先回显供应商报告的实际模型名。
        return PaperTranslationResponse(paper_id=paper.paper_id, field=field, text_zh=translated.text_zh.strip(), model_name=model_name if isinstance(model_name, str) and model_name.strip() else self._config.deepseek_model)  # 返回与保存论文和请求字段绑定的稳定中文结果。


_SYSTEM_PROMPT = """你是严谨的学术翻译器。将输入论文的单个 field 与 text 翻译为简体中文。
保留模型名、数据集名、缩写、公式、数值、引文标记和专有名词；不要概括、评价、补充事实或输出 Markdown。
必须只输出 JSON 对象，格式为：{\"text_zh\": \"...\"}。"""  # 固定提示确保翻译边界清晰且响应可解析。
