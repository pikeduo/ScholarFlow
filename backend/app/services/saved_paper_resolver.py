"""统一解析搜索结果与文献库中的已保存论文快照。"""

from collections.abc import Sequence  # 标注批量读取的请求顺序契约。

from backend.app.models.paper import PaperRecord  # 复用统一的规范化论文领域契约。
from backend.app.repositories.library import LibraryRepository  # 读取用户明确收藏的本地论文快照。
from backend.app.services.search_run_store import SearchRunStateStore  # 读取搜索运行保存的最终结果快照。


class SavedPaperResolver:
    """按“搜索快照优先、文献库回退”规则读取已保存论文。

    参数：
        state_store：搜索运行快照的只读访问边界。
        library_repository：用户已收藏论文快照的只读访问边界。
    异常：
        SearchRunStoreError：由搜索快照存储原样抛出，供 HTTP 路由映射稳定错误。
    """

    def __init__(self, state_store: SearchRunStateStore, library_repository: LibraryRepository) -> None:
        """保存可替换的只读存储依赖，不建立外部来源或模型调用。"""
        self._state_store = state_store  # 保留搜索结果的首选事实来源。
        self._library_repository = library_repository  # 保留仅限收藏记录的安全回退来源。

    def get_paper(self, paper_id: str) -> PaperRecord | None:
        """读取单篇已保存论文，搜索快照未命中时才回退到文献库。

        参数：
            paper_id：需要精确匹配的内部论文标识。
        返回：
            PaperRecord | None：首选搜索快照、收藏快照或未命中的空值。
        """
        paper = self._state_store.get_paper(paper_id)  # 先读取搜索运行内更完整的原始事实快照。
        if paper is not None:  # 搜索快照命中时禁止用文献库记录覆盖它。
            return paper  # 保持详情和翻译的来源优先级一致。
        return self._library_repository.find_paper(paper_id)  # 仅在搜索快照不存在时读取用户收藏快照。

    def get_papers(self, paper_ids: Sequence[str]) -> list[PaperRecord]:
        """批量读取已保存论文，并严格按请求顺序返回已命中的记录。

        参数：
            paper_ids：比较等固定列场景要求保留的论文标识顺序。
        返回：
            list[PaperRecord]：每个已命中标识对应的论文，未命中项由调用方转换为业务 404。
        """
        search_papers = self._state_store.get_papers(paper_ids)  # 批量读取搜索结果以保留原有存储访问效率。
        papers_by_id = {paper.paper_id: paper for paper in search_papers}  # 建立标识索引以屏蔽存储返回顺序。
        for paper_id in paper_ids:  # 逐项检查请求集合，确保只为搜索未命中项执行回退。
            if paper_id in papers_by_id:  # 搜索快照命中时保持其优先级且避免额外 SQLite 查询。
                continue  # 继续处理下一个请求标识。
            library_paper = self._library_repository.find_paper(paper_id)  # 仅查询用户已收藏的本地快照。
            if library_paper is not None:  # 不伪造不存在论文，也不访问外部学术来源。
                papers_by_id[paper_id] = library_paper  # 将实际命中的回退记录纳入统一排序索引。
        return [papers_by_id[paper_id] for paper_id in paper_ids if paper_id in papers_by_id]  # 按调用方请求顺序收敛已命中结果。
