"""验证 LLM 搜索策略提案、硬约束保护和确定性降级。"""

import asyncio  # 在同步 pytest 用例中执行异步策略服务。
import json  # 校验 MockTransport 收到的受限 JSON 请求体。

import httpx  # 使用内存 MockTransport 验证适配器而不访问真实 DeepSeek。

from backend.app.adapters.deepseek_search_strategy import DeepSeekSearchStrategyClient, SearchStrategyError, SearchStrategyProposal  # 构造无需网络的策略客户端替身结果。
from backend.app.core.config import Settings  # 构造不读取本地 .env 的最小测试配置。
from backend.app.models.coverage import CoverageGap, CoverageReport  # 构造下一轮检索所需覆盖缺口。
from backend.app.models.paper import PaperRecord  # 构造只含公开元数据的当前候选。
from backend.app.models.query_intent import QueryIntent, QuerySubquery  # 构造完整查询和模型建议子查询。
from backend.app.services.llm_query_evolution import LlmQueryEvolutionService  # 导入待测 LLM 优先演化服务。


class _StubStrategyClient:
    """返回固定策略提案并记录服务传入的候选数。"""

    def __init__(self, proposal: SearchStrategyProposal | Exception) -> None:
        """保存本用例需要的成功提案或受控失败。"""
        self._proposal = proposal  # 不访问网络、配置或真实模型。
        self.paper_count = 0  # 验证策略服务传递当前候选而非全文数据。

    async def propose(self, query: QueryIntent, coverage_report: CoverageReport, papers: list[PaperRecord], executed_subqueries: list[str]) -> SearchStrategyProposal:
        """返回预设结果并验证调用边界只含必要领域对象。"""
        _ = query, coverage_report, executed_subqueries  # 本替身不读取敏感查询正文。
        self.paper_count = len(papers)  # 记录收到的公开论文候选数量。
        if isinstance(self._proposal, Exception):  # 模拟模型配置、网络或结构故障。
            raise self._proposal  # 交给服务验证安全降级行为。
        return self._proposal  # 返回固定且可审计的模型建议。


def _query() -> QueryIntent:
    """构造带方法和数据集缺口的最小英文检索意图。"""
    return QueryIntent(original_query="检索 Transformer 在 ETT 上的预测论文", normalized_query="time series forecasting Transformer", query_language="mixed", research_topics=["time series forecasting"], methods=["Transformer"], datasets=["ETT"], must_include=["forecasting"], exclude=["survey"])  # 保留必须和排除条件验证模型不能改写它们。


def _report() -> CoverageReport:
    """构造仍需补足 ETT 数据集的可继续覆盖报告。"""
    return CoverageReport(target_count=20, gaps=[CoverageGap(gap_type="dataset", constraint="ETT", severity=0.9, current_match_count=0, recommended_query_focus="ETT")], new_valid_count=1, marginal_gain=0.05, should_continue=True)  # 提供最小可查询缺口。


def _paper() -> PaperRecord:
    """构造仅使用标题和摘要的当前高相关候选。"""
    return PaperRecord(paper_id="paper-1", title="Transformer Forecasting", abstract="A forecasting method evaluated on public benchmarks.", source="openalex")  # 不包含或读取 PDF 全文。


def test_llm_strategy_proposal_has_priority_and_keeps_hard_constraints() -> None:
    """有效模型建议应优先于模板查询，且不能修改用户硬约束。"""
    proposal = SearchStrategyProposal(subqueries=[QuerySubquery(query="ETT benchmark multivariate prediction", language="en", purpose="dataset")], reason="补足尚未覆盖的 ETT 数据集", model_name="deepseek-test", prompt_tokens=120, completion_tokens=30)  # 构造合法且不与已执行表达过度相似的英文策略表达。
    client = _StubStrategyClient(proposal)  # 注入离线策略替身。
    result = asyncio.run(LlmQueryEvolutionService(client=client).evolve(_query(), _report(), papers=[_paper()], executed_subqueries=["time series forecasting Transformer"]))  # 执行 LLM 优先路径。

    assert [item.query for item in result.generated_subqueries] == ["ETT benchmark multivariate prediction"]  # 验证有效策略查询优先进入下一轮。
    assert result.query_intent.must_include == ["forecasting"] and result.query_intent.exclude == ["survey"]  # 验证模型不能放宽或改写硬约束。
    assert result.strategy_model_name == "deepseek-test" and result.strategy_prompt_tokens == 120 and result.strategy_completion_tokens == 30  # 验证策略模型与 Token 会进入后续运行审计。
    assert client.paper_count == 1  # 验证策略仅获得当前公开候选而非额外全文。


def test_llm_strategy_failure_falls_back_to_deterministic_query_evolution() -> None:
    """策略客户端失败时必须保留既有规则化查询演化，不阻断下一轮搜索。"""
    client = _StubStrategyClient(SearchStrategyError("测试策略不可用"))  # 构造已净化的适配器失败。
    result = asyncio.run(LlmQueryEvolutionService(client=client).evolve(_query(), _report(), papers=[_paper()], executed_subqueries=[]))  # 执行会降级的策略路径。

    assert result.generated_subqueries  # 验证确定性演化仍生成可执行补充查询。
    assert any("LLM 搜索策略不可用" in warning for warning in result.warnings)  # 验证降级不会伪装成模型策略成功。
    assert result.strategy_prompt_tokens == 0 and result.strategy_completion_tokens == 0  # 验证失败调用不虚构供应商用量。


def test_deepseek_strategy_adapter_parses_json_and_usage_without_network() -> None:
    """DeepSeek 策略适配器应发送受限 JSON，并解析子查询与 Token 用量。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        """验证请求边界后返回一个可控的兼容 Chat Completions 响应。"""
        request_payload = json.loads(request.content.decode("utf-8"))  # 解码内存请求体，不记录或输出测试密钥。
        assert request.url.path == "/chat/completions" and request_payload["response_format"] == {"type": "json_object"}  # 验证适配器使用严格 JSON 端点与输出约束。
        assert len(json.loads(request_payload["messages"][1]["content"])["papers"]) == 1  # 验证策略上下文仅含当前候选元数据。
        return httpx.Response(200, json={"model": "deepseek-test", "choices": [{"message": {"content": '{"subqueries":[{"query":"ETT forecasting benchmark","language":"en","purpose":"dataset"}],"reason":"补足数据集缺口"}'}}], "usage": {"prompt_tokens": 101, "completion_tokens": 29}})  # 返回最小合法模型 JSON 和用量。

    config = Settings(_env_file=None, deepseek_api_key="test-key")  # 使用内存测试密钥，避免读取或依赖真实环境配置。
    client = DeepSeekSearchStrategyClient(config=config, transport=httpx.MockTransport(handler))  # 注入 MockTransport 防止任何网络访问。
    proposal = asyncio.run(client.propose(_query(), _report(), [_paper()], ["time series forecasting Transformer"]))  # 执行一次受控适配器调用。

    assert proposal.subqueries[0].query == "ETT forecasting benchmark" and proposal.model_name == "deepseek-test"  # 验证结构化策略字段被正确映射。
    assert proposal.prompt_tokens == 101 and proposal.completion_tokens == 29  # 验证供应商用量以非负整数传递给服务层。
