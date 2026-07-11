"""验证 OpenAlex 搜索服务的编排、空结果和客户端异常边界。"""

import asyncio  # 在同步 pytest 用例中执行异步服务方法。

import pytest  # 提供异常断言工具。

from backend.app.models.paper import Paper  # 构造统一论文测试数据。
from backend.app.models.query import QuerySchema  # 构造已校验的服务输入。
from backend.app.services.openalex_search import OpenAlexSearchService  # 导入待测搜索编排服务。


class FakeOpenAlexClient:
    """返回预设论文或异常的离线 OpenAlex 客户端替身。

    参数：
        papers：正常调用时返回的统一论文列表。
        error：需要模拟的客户端失败；存在时优先抛出。
    """

    def __init__(self, papers: list[Paper], error: RuntimeError | None = None) -> None:
        """保存测试所需的固定返回值或异常。"""
        self._papers = papers  # 保留不依赖网络的固定论文列表。
        self._error = error  # 保留可验证传播行为的已净化异常。

    async def search_works(self, query: QuerySchema) -> list[Paper]:
        """返回预设结果，不访问真实 OpenAlex 服务。"""
        if self._error is not None:  # 优先模拟适配层已经净化的失败。
            raise self._error  # 让服务层保留稳定的异常边界。
        return self._papers  # 返回固定的规范化论文列表。


def test_search_service_deduplicates_and_reports_counts() -> None:
    """服务应调用客户端、去重论文并返回前后数量统计。"""
    client = FakeOpenAlexClient(  # 构造包含重复 DOI 的离线客户端。
        papers=[
            Paper(paper_id="W1", title="论文 A", doi="10.1000/example", source="openalex"),  # 提供首次 DOI 论文。
            Paper(paper_id="W2", title="论文 A 副本", doi="doi:10.1000/example", source="openalex"),  # 提供同 DOI 重复论文。
        ]
    )
    service = OpenAlexSearchService(client)  # 注入可替换客户端构造服务。
    result = asyncio.run(service.search(QuerySchema(topic=["forecasting"])))  # 执行一次不访问网络的异步检索。
    assert result.recalled_count == 2  # 验证统计保留客户端的原始召回数量。
    assert result.deduplicated_count == 1  # 验证统计反映去重后的论文数量。
    assert result.filtered_count == 0  # 验证未指定过滤条件时不会移除去重论文。
    assert [paper.paper_id for paper in result.papers] == ["W1"]  # 验证服务保留首次出现的论文。


def test_search_service_returns_empty_result() -> None:
    """客户端无结果时服务应返回合法的零数量结果。"""
    service = OpenAlexSearchService(FakeOpenAlexClient(papers=[]))  # 注入返回空列表的离线客户端。
    result = asyncio.run(service.search(QuerySchema(topic=["forecasting"])))  # 执行空结果检索。
    assert result.papers == []  # 验证空论文列表可安全传递给后续排序阶段。
    assert result.recalled_count == 0  # 验证原始召回统计为零。
    assert result.deduplicated_count == 0  # 验证去重后统计也为零。
    assert result.filtered_count == 0  # 验证空结果不会产生虚假的过滤统计。


def test_search_service_applies_local_filtering_statistics() -> None:
    """服务应在去重后应用本地规则并报告实际过滤数量。"""
    client = FakeOpenAlexClient(  # 构造包含一篇应被排除论文的离线客户端。
        papers=[
            Paper(paper_id="W1", title="Forecasting Method", year=2023, source="openalex"),  # 提供应保留论文。
            Paper(paper_id="W2", title="Forecasting Survey", year=2023, source="openalex"),  # 提供命中排除词论文。
        ]
    )
    service = OpenAlexSearchService(client)  # 注入离线客户端构造服务。
    result = asyncio.run(service.search(QuerySchema(topic=["forecasting"], exclude=["survey"])))  # 执行包含排除词的本地检索。
    assert result.recalled_count == 2  # 验证客户端的原始召回统计。
    assert result.deduplicated_count == 2  # 验证两篇论文不存在稳定标识重复。
    assert result.filtered_count == 1  # 验证服务统计一篇被本地规则移除的论文。
    assert [paper.paper_id for paper in result.papers] == ["W1"]  # 验证仅保留未命中排除词的论文。


def test_search_service_propagates_sanitized_client_error() -> None:
    """适配层失败时服务不应重写或泄露底层请求细节。"""
    client = FakeOpenAlexClient(papers=[], error=RuntimeError("OpenAlex 网络请求失败"))  # 构造会失败的离线客户端。
    service = OpenAlexSearchService(client)  # 注入失败客户端构造服务。
    with pytest.raises(RuntimeError, match="OpenAlex 网络请求失败"):  # 断言已净化的异常会被调用方接收。
        asyncio.run(service.search(QuerySchema(topic=["forecasting"])))  # 执行并触发客户端失败边界。
