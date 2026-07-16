"""验证统一已保存论文解析服务的来源优先级、顺序和异常透传边界。"""

from collections.abc import Sequence  # 标注测试替身接收的批量标识顺序。

import pytest  # 验证存储异常不被服务层改写。

from backend.app.models.paper import PaperRecord  # 构造无需外部服务的规范化论文事实。
from backend.app.services.saved_paper_resolver import SavedPaperResolver, SavedPaperScope  # 覆盖统一解析服务的公开行为和显式读取范围。
from backend.app.services.search_run_store import SearchRunStoreError  # 模拟搜索快照存储的稳定错误边界。


class FakeSearchRunStore:
    """提供可记录调用顺序的搜索快照只读替身。"""

    def __init__(self, single_paper: PaperRecord | None = None, batch_papers: list[PaperRecord] | None = None, should_fail: bool = False) -> None:
        """保存单篇、批量快照结果及可控的存储失败开关。"""
        self._single_paper = single_paper  # 保存单篇读取的搜索快照结果。
        self._batch_papers = batch_papers or []  # 保存批量读取的搜索快照结果。
        self._should_fail = should_fail  # 保存是否模拟存储异常。
        self.batch_requests: list[list[str]] = []  # 记录批量读取收到的原始请求顺序。

    def get_paper(self, _: str) -> PaperRecord | None:
        """返回单篇搜索快照或按需抛出统一存储异常。"""
        if self._should_fail:  # 在异常边界用例中阻断后续文献库回退。
            raise SearchRunStoreError("模拟搜索快照读取失败")  # 保持生产存储异常类型。
        return self._single_paper  # 返回预置的搜索快照结果。

    def get_papers(self, paper_ids: Sequence[str]) -> list[PaperRecord]:
        """记录请求顺序后返回预置批量快照或抛出统一存储异常。"""
        self.batch_requests.append(list(paper_ids))  # 锁定服务未改变调用方提交的批量顺序。
        if self._should_fail:  # 在异常边界用例中不读取文献库。
            raise SearchRunStoreError("模拟搜索快照读取失败")  # 保持生产存储异常类型。
        return self._batch_papers  # 故意允许替身以非请求顺序返回记录。


class FakeLibraryRepository:
    """提供可记录回退范围的用户收藏论文快照替身。"""

    def __init__(self, papers: list[PaperRecord] | None = None) -> None:
        """按论文标识保存可用于安全回退的本地快照。"""
        self._papers = {paper.paper_id: paper for paper in papers or []}  # 建立精确标识的本地快照索引。
        self.requested_ids: list[str] = []  # 记录实际触发文献库回退的论文标识。

    def find_paper(self, paper_id: str) -> PaperRecord | None:
        """记录读取请求并返回对应收藏快照或空值。"""
        self.requested_ids.append(paper_id)  # 验证搜索快照命中时不会发生回退查询。
        return self._papers.get(paper_id)  # 模拟生产仓储的精确本地查找。


def _paper(paper_id: str, title: str) -> PaperRecord:
    """构造仅含最小公开元数据的已保存论文事实。"""
    return PaperRecord(paper_id=paper_id, title=title, source="openalex")  # 保持测试完全离线且不依赖数据库。


def test_get_paper_prefers_search_snapshot_before_library_fallback() -> None:
    """同一论文同时存在时，详情读取必须保留搜索快照而不访问文献库。"""
    search_paper = _paper("paper-1", "搜索快照标题")  # 构造首选搜索事实。
    library = FakeLibraryRepository([_paper("paper-1", "收藏快照标题")])  # 构造不应被读取的同标识回退事实。
    result = SavedPaperResolver(FakeSearchRunStore(single_paper=search_paper), library).get_paper("paper-1")  # 执行单篇统一解析。
    assert result == search_paper  # 锁定搜索快照优先于文献库的业务规则。
    assert library.requested_ids == []  # 锁定命中搜索快照时不会发生无效回退查询。


def test_get_paper_falls_back_to_library_after_search_snapshot_miss() -> None:
    """单篇详情与翻译读取在搜索快照清理后必须可回退到收藏快照。"""
    library_paper = _paper("paper-1", "收藏快照标题")  # 构造仅存在于用户收藏中的论文事实。
    library = FakeLibraryRepository([library_paper])  # 注入可验证读取范围的本地快照替身。
    result = SavedPaperResolver(FakeSearchRunStore(), library).get_paper("paper-1")  # 执行搜索未命中后的统一回退。
    assert result == library_paper  # 锁定回退返回原始收藏快照而不伪造论文事实。
    assert library.requested_ids == ["paper-1"]  # 锁定只对搜索未命中的标识发起一次回退查询。


def test_search_only_scope_never_reads_library_fallback() -> None:
    """引用图和技术路线使用搜索专用范围时不得读取用户收藏快照。"""
    library = FakeLibraryRepository([_paper("paper-1", "不应读取的收藏论文")])  # 构造若范围错误便会被命中的收藏记录。
    result = SavedPaperResolver(FakeSearchRunStore(), library).get_papers(["paper-1"], scope=SavedPaperScope.SEARCH_ONLY)  # 使用搜索专用范围读取未命中论文。
    assert result == []  # 锁定未命中不被收藏快照填充。
    assert library.requested_ids == []  # 锁定搜索专用范围不会访问文献库。


def test_get_paper_propagates_search_storage_error_without_library_fallback() -> None:
    """单篇搜索快照读取失败不是未命中，必须由路由映射既有 503。"""
    library = FakeLibraryRepository([_paper("paper-1", "收藏论文")])  # 构造不应在存储故障时读取的回退记录。
    resolver = SavedPaperResolver(FakeSearchRunStore(should_fail=True), library)  # 注入会抛出稳定存储错误的搜索快照替身。
    with pytest.raises(SearchRunStoreError):  # 锁定服务不吞没或改写原有存储错误。
        resolver.get_paper("paper-1")  # 触发单篇搜索快照读取故障。
    assert library.requested_ids == []  # 锁定故障不会被误判为未命中后继续读取文献库。


def test_get_papers_falls_back_only_for_missing_items_and_preserves_requested_order() -> None:
    """批量读取应只回退未命中项，并消除仓储返回顺序对比较固定列的影响。"""
    paper_one = _paper("paper-1", "收藏论文")  # 构造仅存在于文献库的首个请求项。
    paper_two = _paper("paper-2", "搜索论文二")  # 构造搜索快照命中的中间请求项。
    paper_three = _paper("paper-3", "搜索论文三")  # 构造搜索快照命中的末尾请求项。
    search_store = FakeSearchRunStore(batch_papers=[paper_three, paper_two])  # 故意倒序返回以验证服务重排职责。
    library = FakeLibraryRepository([paper_one, _paper("paper-2", "不应覆盖的收藏论文")])  # 同时提供不应查询的搜索命中项。
    result = SavedPaperResolver(search_store, library).get_papers(["paper-1", "paper-2", "paper-3"])  # 执行比较场景的批量统一解析。
    assert search_store.batch_requests == [["paper-1", "paper-2", "paper-3"]]  # 锁定传入搜索存储的请求顺序。
    assert library.requested_ids == ["paper-1"]  # 锁定仅搜索未命中项触发文献库回退。
    assert [paper.paper_id for paper in result] == ["paper-1", "paper-2", "paper-3"]  # 锁定返回结果与调用方固定列顺序一致。
    assert result[1].title == "搜索论文二"  # 锁定搜索快照不会被同标识收藏快照覆盖。


def test_get_papers_propagates_search_storage_error_without_library_fallback() -> None:
    """搜索快照故障必须由路由映射为既有 503，服务不得改写或掩盖异常。"""
    library = FakeLibraryRepository([_paper("paper-1", "收藏论文")])  # 构造不应在搜索故障后读取的回退数据。
    resolver = SavedPaperResolver(FakeSearchRunStore(should_fail=True), library)  # 注入会抛出稳定存储异常的替身。
    with pytest.raises(SearchRunStoreError):  # 锁定异常直接交给各路由保持现有 HTTP 语义。
        resolver.get_papers(["paper-1", "paper-2"])  # 触发批量搜索快照读取失败。
    assert library.requested_ids == []  # 锁定存储故障不会被误判为普通未命中并回退。
