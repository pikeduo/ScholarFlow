"""提供文献库自然语言语义检索、SQLite 映射过滤和本地降级匹配。"""

from dataclasses import dataclass  # 使用内部值对象保留候选收藏与原始分数。
from re import findall  # 从自然语言查询提取稳定的降级词项。
from typing import Protocol  # 声明可替换语义检索器边界供 API 测试覆盖。

from backend.app.core.logging import logger  # 记录数量与降级状态，不记录完整自然语言查询。
from backend.app.models.library import LibraryItem, LibrarySemanticSearchItem, LibrarySemanticSearchResult  # 返回稳定 API 响应领域模型。
from backend.app.repositories.faiss_index import FaissIndexError, FaissIndexManager, IndexSearchHit  # 执行 Top-Kx 向量候选查询。
from backend.app.repositories.vector_metadata import VectorMetadataRepository  # 将 FAISS ID 过滤并映射回当前论文快照。
from backend.app.services.embedding import EmbeddingService, EmbeddingServiceError  # 生成查询 BGE-M3 向量。
from backend.app.services.library_vector_index import DEFAULT_LIBRARY_INDEX_PATH, LIBRARY_INDEX_NAME  # 复用收藏写入使用的索引名称与文件路径。


DEFAULT_LIBRARY_SEMANTIC_TOP_K = 20  # 将文献库自然语言检索默认结果数与产品目标保持一致。
DEFAULT_LIBRARY_CANDIDATE_MULTIPLIER = 5  # 预留足够 FAISS 候选抵消 SQLite 与结构化筛选。


@dataclass(frozen=True)
class _ScoredLibraryItem:
    """保存内部排序所需的收藏对象和归一化分数。"""

    item: LibraryItem  # 保存可直接转换为公共响应的收藏记录。
    score: float  # 保存向量或降级词项计算的相似度分数。


class LibrarySemanticSearcher(Protocol):
    """约束 LibraryService 使用的最小自然语言语义搜索能力。"""

    async def search(self, query: str, items: list[LibraryItem], metadata_repository: VectorMetadataRepository, top_k: int) -> LibrarySemanticSearchResult:
        """在已完成结构化筛选的收藏集合中执行语义检索。"""
        ...  # Protocol 允许 API 测试替换模型与 FAISS 实现。


class LibrarySemanticSearchService:
    """通过 Query BGE-M3、Library FAISS 和 SQLite 活动映射检索收藏论文。

    参数：
        embedding_service：可替换查询向量编码服务。
        index_manager：复用文献库收藏写入的 FAISS 索引管理器。
        candidate_multiplier：每个目标结果额外读取的向量候选倍数。
    """

    def __init__(self, embedding_service: EmbeddingService | None = None, index_manager: FaissIndexManager | None = None, candidate_multiplier: int = DEFAULT_LIBRARY_CANDIDATE_MULTIPLIER) -> None:
        """保存可替换依赖，不在构造阶段加载模型或读取索引文件。"""
        if candidate_multiplier < 1:  # 零候选倍数无法处理 SQLite 逻辑失效和标签过滤。
            raise ValueError("candidate_multiplier 必须大于零")  # 尽早暴露无效检索策略。
        self._embedding_service = embedding_service or EmbeddingService()  # 默认使用阶段五懒加载 BGE 查询编码器。
        self._index_manager = index_manager or FaissIndexManager(LIBRARY_INDEX_NAME, DEFAULT_LIBRARY_INDEX_PATH)  # 默认指向与收藏写入相同的文献库索引文件。
        self._candidate_multiplier = candidate_multiplier  # 保存为结构化过滤预留的 FAISS 候选规模。

    async def search(self, query: str, items: list[LibraryItem], metadata_repository: VectorMetadataRepository, top_k: int = DEFAULT_LIBRARY_SEMANTIC_TOP_K) -> LibrarySemanticSearchResult:
        """在给定收藏子集上执行 BGE-M3 与 FAISS 语义检索，失败时降级为本地词项匹配。"""
        if top_k < 1:  # 零目标数量没有 API 语义。
            raise ValueError("top_k 必须大于零")  # 在模型与索引调用前返回稳定输入错误。
        if not items:  # 结构化筛选后没有收藏无需加载模型。
            return LibrarySemanticSearchResult()  # 返回稳定空结果。
        try:  # 将模型和索引不可用转为不阻断文献库的本地降级。
            embedding_batch = await self._embedding_service.encode_queries([query])  # 生成单条自然语言查询的单位向量。
            candidate_limit = top_k * self._candidate_multiplier  # 显式执行 Top-Kx，为 SQLite 和筛选补足预留空间。
            faiss_hits = self._index_manager.search(list(embedding_batch.vectors[0]), top_k=candidate_limit, candidate_multiplier=1)  # 先读取原始 FAISS 候选，随后统一应用 SQLite 状态与结构化筛选。
            result_items = self._map_hits(faiss_hits, items, metadata_repository, top_k)  # 映射稳定 ID、过滤 inactive 和非当前筛选集合。
        except (EmbeddingServiceError, FaissIndexError, ValueError, RuntimeError):  # 捕获净化模型、索引、异步和输入错误。
            logger.exception("文献库语义检索降级：候选收藏数=%d，目标数=%d", len(items), top_k)  # 不记录用户原始查询或论文正文。
            return _build_degraded_result(query, items, top_k)  # 使用本地公开元数据词项匹配维持可用性。
        logger.info("文献库语义检索完成：候选收藏数=%d，FAISS候选数=%d，返回数=%d", len(items), len(faiss_hits), len(result_items))  # 记录阶段数量统计。
        return _build_result(result_items)  # 返回未降级的按相似度排序结果。

    def _map_hits(self, hits: list[IndexSearchHit], items: list[LibraryItem], metadata_repository: VectorMetadataRepository, top_k: int) -> list[_ScoredLibraryItem]:
        """用 SQLite active 映射和当前结构化筛选集合过滤 FAISS 候选并回填收藏。"""
        item_by_paper_id = {item.paper.paper_id: item for item in items}  # 仅允许当前标签和阅读状态筛选后的收藏进入结果。
        records_by_vector_id = metadata_repository.active_records(self._index_manager.index_name, [hit.vector_id for hit in hits])  # 过滤 pending、failed 和 inactive 向量并读取论文映射。
        scored_items: list[_ScoredLibraryItem] = []  # 保存保持 FAISS 分数顺序的公共收藏候选。
        seen_item_ids: set[str] = set()  # 防止历史多向量或来源更新造成同一收藏重复展示。
        for hit in hits:  # FAISS 已按内积降序排列候选。
            record = records_by_vector_id.get(hit.vector_id)  # 查询当前向量是否仍 active。
            if record is None:  # SQLite 映射不存在或已失效时跳过。
                continue  # 防止逻辑删除论文被旧 FAISS 向量召回。
            item = item_by_paper_id.get(record.paper_id)  # 仅保留本次结构化筛选允许的收藏论文。
            if item is None or item.item_id in seen_item_ids:  # 过滤非当前筛选集合与重复收藏项。
                continue  # 继续寻找可补足的有效候选。
            scored_items.append(_ScoredLibraryItem(item=item, score=_clamp_score(hit.score)))  # 保存安全归一化后的向量相似度。
            seen_item_ids.add(item.item_id)  # 标记收藏已返回，避免多向量重复。
            if len(scored_items) == top_k:  # 已填满目标结果数时停止遍历候选。
                break  # 控制响应规模。
        return scored_items  # 返回保留 FAISS 排序的有效收藏。


def _build_result(scored_items: list[_ScoredLibraryItem]) -> LibrarySemanticSearchResult:
    """将内部候选转换为稳定的未降级 API 响应。"""
    items = [LibrarySemanticSearchItem(item=scored_item.item, semantic_score=scored_item.score) for scored_item in scored_items]  # 仅暴露收藏详情和可解释相似度。
    return LibrarySemanticSearchResult(items=items, total=len(items))  # 不回显完整用户自然语言查询。


def _build_degraded_result(query: str, items: list[LibraryItem], top_k: int) -> LibrarySemanticSearchResult:
    """在 BGE 或 FAISS 不可用时使用标题、摘要和关键词的确定性词项匹配。"""
    terms = {term.casefold() for term in findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1}  # 提取不含单字符噪声的中英文词项。
    scored_items: list[_ScoredLibraryItem] = []  # 收集具有至少一个词项命中的收藏。
    for item in items:  # 仅在调用方已完成的结构化筛选子集内降级匹配。
        searchable_text = " ".join((item.paper.title, item.paper.abstract, " ".join(item.paper.keywords))).casefold()  # 只使用公开论文元数据，不读取个人备注。
        matched_count = sum(term in searchable_text for term in terms)  # 计算确定性词项命中数量。
        if matched_count:  # 没有匹配词项的论文不应用低相关结果凑数。
            score = matched_count / len(terms) if terms else 0.0  # 归一化为稳定的零到一分数。
            scored_items.append(_ScoredLibraryItem(item=item, score=score))  # 保存词项匹配候选。
    ranked_items = sorted(scored_items, key=lambda scored_item: (-scored_item.score, scored_item.item.item_id))[:top_k]  # 使用分数和收藏 ID 提供跨运行稳定排序。
    result = _build_result(ranked_items)  # 复用公共响应转换逻辑。
    return result.model_copy(update={"degraded": True, "degradation_reason": "语义索引暂不可用，已按论文元数据词项匹配"})  # 返回不泄露模型、索引路径或底层异常的降级原因。


def _clamp_score(score: float) -> float:
    """将浮点内积分数收敛到 API 契约要求的零到一区间。"""
    return max(0.0, min(1.0, float(score)))  # 防御浮点误差或非归一化历史索引导致的轻微越界。
