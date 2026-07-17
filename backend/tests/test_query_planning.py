"""验证自然语言 Query Agent 的英文检索式、显式覆盖和召回规模。"""

import asyncio  # 在同步 pytest 中运行异步规划客户端。
import json  # 构造 DeepSeek JSON Output 测试响应。

import httpx  # 使用 MockTransport 禁止真实网络访问。

from backend.app.adapters.deepseek_query_planner import DeepSeekQueryPlanningClient  # 导入待测查询规划适配器。
from backend.app.core.config import Settings  # 构造隔离配置。
from backend.app.models.natural_search import NaturalSearchRequest  # 构造自然语言请求。


def test_query_planner_generates_english_intent_and_preserves_explicit_constraints() -> None:
    """Query Agent 应生成英文检索字段，并让用户显式条件覆盖模型推断。"""
    planned_json = json.dumps(  # 构造模型返回的完整结构化计划。
        {
            "normalized_query": "vision language models medical image report generation public datasets",
            "query_language": "zh",
            "research_topics": ["vision-language models", "medical imaging"],
            "methods": ["vision-language model"],
            "tasks": ["medical image report generation"],
            "datasets": [],
            "authors": [],
            "institutions": [],
            "venues": [],
            "paper_types": [],
            "year_range": [2022, 2026],
            "must_include": ["vision-language model"],
            "should_include": ["public dataset"],
            "exclude": [],
            "domains": ["artificial intelligence", "medical imaging"],
            "complexity_score": 0.6,
            "subqueries": [{"query": "vision language model medical report generation", "language": "en", "purpose": "method"}],
        },
        ensure_ascii=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        """验证 JSON Output 请求并返回离线规划。"""
        body = json.loads(request.content.decode("utf-8"))  # 使用显式 UTF-8 读取请求。
        assert body["response_format"] == {"type": "json_object"}  # 验证启用结构化输出。
        return httpx.Response(200, json={"model": "deepseek-v4-flash", "usage": {"prompt_tokens": 321, "completion_tokens": 123}, "choices": [{"message": {"content": planned_json}}]}, request=request)  # 返回固定规划及用量统计。

    config = Settings(_env_file=None, deepseek_api_key="test-key", academic_source_recall_limit=50)  # 注入无权限测试密钥和召回规模。
    client = DeepSeekQueryPlanningClient(config=config, transport=httpx.MockTransport(handler))  # 构造离线客户端。
    request = NaturalSearchRequest(query="检索视觉语言模型在医学影像报告生成中的最新研究，优先包含公开数据集", year_range=(2023, 2026), must_include=["medical imaging"], exclude=["survey"], search_mode="standard", enable_semantic_ranking=False, enable_cross_encoder_ranking=True)  # 提供显式约束和独立排序选择。
    planning_result = asyncio.run(client.plan(request))  # 执行不访问网络的规划。
    intent = planning_result.query_intent  # 提取可执行意图供语义字段断言。

    assert intent.normalized_query.startswith("vision language models")  # 验证学术 API 使用英文检索式。
    assert intent.tasks == ["medical image report generation"]  # 验证目标任务被独立提取。
    assert intent.should_include == ["public dataset"]  # 验证“优先”保持软偏好。
    assert intent.must_include == ["medical imaging"]  # 验证只有用户显式高级条件进入逐字硬过滤。
    assert intent.enable_semantic_ranking is False  # 验证 BGE-M3 关闭选择穿透 Query Agent。
    assert intent.enable_cross_encoder_ranking is True  # 验证 Cross Encoder 开启选择穿透 Query Agent。
    assert intent.year_range == (2023, 2026)  # 验证显式年份覆盖模型推断。
    assert intent.exclude == ["survey"]  # 验证显式排除条件被保留。
    assert intent.source_recall_count == 50 and intent.target_paper_count == 20  # 验证召回规模与最终数量已分离。
    assert planning_result.model_name == "deepseek-v4-flash"  # 验证实际响应模型名称被保留。
    assert planning_result.prompt_tokens == 321 and planning_result.completion_tokens == 123  # 验证查询规划 Token 用量被保留。
    assert planning_result.duration_ms >= 0  # 验证单调时钟耗时以非负毫秒返回。


def test_query_planner_normalizes_real_model_output_variants() -> None:
    """适配器应兼容真实模型返回的论文类型别名、五级复杂度和缺失子查询语言。"""
    planned_json = json.dumps(  # 复现日志中导致 503 的真实输出形状。
        {
            "normalized_query": "large language model multivariate time series forecasting",
            "query_language": "zh",
            "research_topics": ["multivariate time series forecasting"],
            "methods": ["large language model"],
            "tasks": ["forecasting"],
            "datasets": ["ETT"],
            "authors": [],
            "institutions": [],
            "venues": [],
            "paper_types": ["research article"],
            "year_range": {"start": 2021, "end": 2026},
            "must_include": [],
            "should_include": [],
            "exclude": [],
            "domains": ["artificial intelligence"],
            "complexity_score": 3,
            "subqueries": [
                {"query": "multivariate time series forecasting LLM", "purpose": "method"},
                {"query": "large language model ETT forecasting", "purpose": "dataset"},
            ],
        },
        ensure_ascii=False,
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": planned_json}}]}, request=request))  # 返回完全离线的真实变体。
    config = Settings(_env_file=None, deepseek_api_key="test-key", academic_source_recall_limit=50)  # 构造隔离配置。
    client = DeepSeekQueryPlanningClient(config=config, transport=transport)  # 构造不访问网络的规划客户端。

    planning_result = asyncio.run(client.plan(NaturalSearchRequest(query="使用大语言模型进行多变量时间序列预测")))  # 执行规范化与严格领域校验。
    intent = planning_result.query_intent  # 提取规范化后的领域契约。

    assert intent.paper_types == ["article"]  # 验证 research article 映射为核心枚举。
    assert intent.year_range == (2021, 2026)  # 验证 start/end 对象会规范化为领域年份闭区间。
    assert intent.complexity_score == 0.6  # 验证五级复杂度转换为 0–1。
    assert [subquery.language for subquery in intent.subqueries] == ["en", "en"]  # 验证缺失语言默认补为英文。
