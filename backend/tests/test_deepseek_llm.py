"""验证 DeepSeek 论文核验适配器的请求契约、响应映射与错误净化。"""

import asyncio  # 在同步 pytest 用例中执行异步适配器。
import json  # 构造官方 Chat Completions 风格的测试响应。

import httpx  # 使用 MockTransport 截获请求，禁止测试访问真实网络。
import pytest  # 提供适配器错误断言。

from backend.app.adapters.deepseek_llm import DeepSeekPaperAssessmentClient, LlmAssessmentError  # 导入待测 DeepSeek 适配器及安全异常。
from backend.app.core.config import Settings  # 构造不读取本地 .env 的隔离配置。
from backend.app.models.paper import PaperRecord  # 构造发送给模型的公开论文元数据。
from backend.app.models.query_intent import QueryIntent  # 构造统一查询意图。


def _query() -> QueryIntent:
    """构造适配器请求所需的最小查询意图。"""
    return QueryIntent(original_query="中文原始查询", normalized_query="Transformer forecasting", query_language="mixed", must_include=["Transformer"])  # 验证适配器使用规范化查询和结构化约束。


def _paper() -> PaperRecord:
    """构造只含公开字段的最小论文候选。"""
    return PaperRecord(paper_id="paper-1", title="Transformer Forecasting", abstract="Evaluation on ETT.", source="openalex")  # 提供模型核验所需元数据。


def test_client_maps_json_output_and_usage_without_network() -> None:
    """适配器应发送 JSON Output 请求并映射逐篇结果和 Token 统计。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """验证请求边界并返回固定 DeepSeek 风格响应。"""
        assert request.url.path == "/chat/completions"  # 验证使用官方聊天端点。
        assert request.headers["Authorization"] == "Bearer test-deepseek-key"  # 验证密钥仅进入 Bearer 请求头。
        body = json.loads(request.content.decode("utf-8"))  # 使用显式 UTF-8 检查请求正文。
        assert body["model"] == "deepseek-v4-flash"  # 验证默认模型来自集中配置。
        assert body["response_format"] == {"type": "json_object"}  # 验证启用官方 JSON Output。
        assert body["max_tokens"] == 4000  # 验证单个论文核验批次使用受控输出上限。
        assert "Transformer forecasting" in body["messages"][1]["content"]  # 验证发送规范化查询而非日志数据。
        content = json.dumps({"assessments": [{"paper_id": "paper-1", "relevance_score": 0.92, "constraint_status": "satisfied", "evidence": ["Transformer Forecasting"], "recommendation_reason": "主题直接相关。"}]}, ensure_ascii=False)  # 构造合法模型 JSON 内容。
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "model": "deepseek-v4-flash", "usage": {"prompt_tokens": 80, "completion_tokens": 30}})  # 返回非流式成功响应。

    config = Settings(_env_file=None, deepseek_api_key="test-deepseek-key")  # 禁止读取用户本地密钥并注入无权限测试值。
    client = DeepSeekPaperAssessmentClient(config=config, transport=httpx.MockTransport(handler))  # 注入完全离线传输层。
    result = asyncio.run(client.assess(_query(), [_paper()]))  # 执行不访问网络的适配器调用。

    assert result.assessments[0].paper_id == "paper-1"  # 验证结构化论文 ID 映射。
    assert result.assessments[0].relevance_score == 0.92  # 验证相关性分数映射。
    assert result.prompt_tokens == 80 and result.completion_tokens == 30  # 验证 Token 统计映射。


def test_client_repairs_unescaped_quotes_in_model_json_output() -> None:
    """模型偶发遗漏字符串引号转义时，适配器应修复格式后仍执行完整字段校验。"""
    malformed_content = '''```json
{"assessments":[{"paper_id":"paper-1","relevance_score":0.92,"constraint_status":"satisfied","evidence":["Transformer "Forecasting""],"recommendation_reason":"标题包含 "Transformer"，与查询直接相关。"}]}
```'''  # 构造围栏与字符串内部未转义双引号并存的真实模型常见瑕疵。
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": malformed_content}}], "model": "deepseek-v4-flash", "usage": {"prompt_tokens": 80, "completion_tokens": 30}}))  # 使用离线响应复现无效 JSON。
    client = DeepSeekPaperAssessmentClient(config=Settings(_env_file=None, deepseek_api_key="test-key"), transport=transport)  # 注入隔离配置与无网络传输层。

    result = asyncio.run(client.assess(_query(), [_paper()]))  # 执行受限本地修复后的完整解析。

    assert result.assessments[0].evidence == ['Transformer "Forecasting"']  # 验证证据中的字面量引号未被丢失。
    assert result.assessments[0].recommendation_reason == '标题包含 "Transformer"，与查询直接相关。'  # 验证理由中的字面量引号被正确保留。


def test_client_rejects_missing_api_key_before_request() -> None:
    """缺少 DeepSeek 密钥时适配器应返回已净化配置错误。"""
    client = DeepSeekPaperAssessmentClient(config=Settings(_env_file=None))  # 构造无密钥且不读取本地环境文件的配置。

    with pytest.raises(LlmAssessmentError, match="未配置"):  # 断言不会泄露环境变量内容。
        asyncio.run(client.assess(_query(), [_paper()]))  # 在任何网络请求前触发配置校验。


def test_client_rejects_invalid_model_json_without_exposing_response() -> None:
    """模型输出不是目标 JSON 结构时适配器应返回安全错误。"""
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}], "model": "deepseek-v4-flash"}))  # 构造不访问网络的无效内容响应。
    client = DeepSeekPaperAssessmentClient(config=Settings(_env_file=None, deepseek_api_key="test-key"), transport=transport)  # 注入隔离配置和传输层。

    with pytest.raises(LlmAssessmentError, match="无效的论文核验结果"):  # 断言错误信息不包含原始响应正文。
        asyncio.run(client.assess(_query(), [_paper()]))  # 触发嵌套 JSON 校验失败。
