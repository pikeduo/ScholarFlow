"""验证已接入学术来源在用户显式授权时能够完成最小在线检索。"""

import asyncio  # 在同步 pytest 用例中执行各适配器的异步搜索接口。
import os  # 仅读取非敏感的测试授权开关，默认不发起网络请求。

import pytest  # 声明在线测试标记、跳过条件和断言工具。

from backend.app.adapters.arxiv import ArxivClient  # 调用真实 arXiv 适配器进行最小检索。
from backend.app.adapters.dblp import DblpClient  # 调用真实 DBLP 适配器进行最小检索。
from backend.app.adapters.openalex import OpenAlexClient  # 调用真实 OpenAlex 适配器进行最小检索。
from backend.app.adapters.semantic_scholar import SemanticScholarClient  # 调用真实 Semantic Scholar 适配器进行最小检索。
from backend.app.models.paper import PaperRecord, PaperSource  # 校验统一论文模型和来源标识。
from backend.app.models.query_intent import QueryIntent  # 构造所有适配器共用的最小查询契约。


ONLINE_SMOKE_ENVIRONMENT_VARIABLE = "SCHOLARFLOW_RUN_ONLINE_SOURCE_SMOKE_TESTS"  # 仅显式授权时才允许测试访问第三方 API。
ONLINE_SMOKE_ENABLED = os.getenv(ONLINE_SMOKE_ENVIRONMENT_VARIABLE, "").strip().casefold() == "true"  # 默认关闭真实网络调用和配额消耗。
pytestmark = pytest.mark.online  # 将整个文件标记为需用户手动执行的在线测试。


def _smoke_query() -> QueryIntent:
    """构造命中稳定且每来源只请求一篇论文的最小检索意图。

    返回：
        QueryIntent：使用公开计算机科学术语的最小统一查询。
    """
    return QueryIntent(  # 构造同时满足所有来源适配器输入要求的查询。
        original_query="Transformer",  # 提供可审计的简短原始查询。
        normalized_query="Transformer",  # 提供来源搜索使用的英文规范化查询。
        query_language="en",  # 标记查询语言以满足领域契约。
        research_topics=["Transformer"],  # 为各适配器提供明确的主题词。
        target_paper_count=1,  # 限制最终候选数量，避免测试扩大调用规模。
        source_recall_count=1,  # 限制每个来源的网络响应最多映射一篇论文。
    )


def _assert_mapped_papers(papers: list[PaperRecord], source: PaperSource) -> None:
    """校验来源成功响应已映射为带有预期来源的统一论文记录。

    参数：
        papers：适配器返回的统一论文记录。
        source：当前 smoke 测试所验证的来源名称。
    """
    assert papers, f"{source} 未返回可映射论文，请检查 API 凭据、网络和来源状态"  # 空结果无法证明检索与映射链路正常。
    assert all(paper.source == source for paper in papers)  # 防止适配器错误标记论文来源。
    assert all(paper.paper_id and paper.title for paper in papers)  # 验证映射后保留稳定标识和可展示标题。


@pytest.mark.skipif(not ONLINE_SMOKE_ENABLED, reason=f"仅在 {ONLINE_SMOKE_ENVIRONMENT_VARIABLE}=true 时执行真实学术 API 调用")
def test_openalex_online_smoke_search() -> None:
    """OpenAlex 应以当前本地配置完成一次最小论文检索。"""
    papers = asyncio.run(OpenAlexClient().search(_smoke_query()))  # 使用真实适配器调用一次 OpenAlex 搜索。
    _assert_mapped_papers(papers, "openalex")  # 校验响应已映射为统一论文记录。


@pytest.mark.skipif(not ONLINE_SMOKE_ENABLED, reason=f"仅在 {ONLINE_SMOKE_ENVIRONMENT_VARIABLE}=true 时执行真实学术 API 调用")
def test_semantic_scholar_online_smoke_search() -> None:
    """Semantic Scholar 应以当前本地配置完成一次最小论文检索。"""
    papers = asyncio.run(SemanticScholarClient().search(_smoke_query()))  # 使用真实适配器调用一次 Semantic Scholar 搜索。
    _assert_mapped_papers(papers, "semantic_scholar")  # 校验响应已映射为统一论文记录。


@pytest.mark.skipif(not ONLINE_SMOKE_ENABLED, reason=f"仅在 {ONLINE_SMOKE_ENVIRONMENT_VARIABLE}=true 时执行真实学术 API 调用")
def test_arxiv_online_smoke_search() -> None:
    """arXiv 应以当前本地配置完成一次最小论文检索。"""
    papers = asyncio.run(ArxivClient().search(_smoke_query()))  # 使用真实适配器调用一次 arXiv 搜索。
    _assert_mapped_papers(papers, "arxiv")  # 校验响应已映射为统一论文记录。


@pytest.mark.skipif(not ONLINE_SMOKE_ENABLED, reason=f"仅在 {ONLINE_SMOKE_ENVIRONMENT_VARIABLE}=true 时执行真实学术 API 调用")
def test_dblp_online_smoke_search() -> None:
    """DBLP 应以当前本地配置完成一次最小论文检索。"""
    papers = asyncio.run(DblpClient().search(_smoke_query()))  # 使用真实适配器调用一次 DBLP 搜索。
    _assert_mapped_papers(papers, "dblp")  # 校验响应已映射为统一论文记录。
