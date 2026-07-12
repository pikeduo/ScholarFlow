"""验证 FAISS 索引管理器的原子保存、查询、失效过滤和维度边界。"""

from copy import deepcopy  # 在内存替身中模拟 FAISS 文件读写后的独立索引对象。
from dataclasses import dataclass, field  # 构造不依赖 faiss-cpu 的最小索引替身。
from pathlib import Path  # 使用 pytest 临时目录验证原子发布文件路径。

import pytest  # 提供稳定错误边界断言。

from backend.app.repositories.faiss_index import FaissIndexError, FaissIndexManager  # 导入待测索引管理器。


@dataclass
class _MemoryIndex:
    """保存二维内存向量和稳定 ID，模拟 IndexIDMap2 的必要状态。"""

    dimension: int  # 保存索引固定维度。
    vectors: dict[int, list[float]] = field(default_factory=dict)  # 保存 ID 到向量的映射。


class _MemoryFaissBackend:
    """使用标准库模拟 FAISS 后端，避免测试依赖本机二进制扩展。"""

    def __init__(self) -> None:
        """初始化模拟磁盘文件对应的索引快照。"""
        self._saved: dict[str, _MemoryIndex] = {}  # 保存路径到独立索引快照的映射。

    def create(self, dimension: int) -> _MemoryIndex:
        """创建指定维度的空内存索引。"""
        return _MemoryIndex(dimension=dimension)  # 不访问真实 FAISS。

    def read(self, path: Path) -> _MemoryIndex:
        """读取写入路径对应的独立索引快照。"""
        return deepcopy(self._saved[str(path)])  # 模拟文件读取不会共享进程内可变状态。

    def write(self, index: _MemoryIndex, path: Path) -> None:
        """保存内存快照并创建占位文件，模拟完整临时索引写入。"""
        self._saved[str(path)] = deepcopy(index)  # 保存临时路径可供管理器复读校验。
        if path.suffix == ".tmp":  # 原子替换后正式路径会移除临时后缀。
            self._saved[str(path.with_suffix(""))] = deepcopy(index)  # 预置正式路径快照供新管理器加载。
        path.write_text("memory-faiss", encoding="utf-8")  # 创建同目录临时文件供 os.replace 执行。

    def add_with_ids(self, index: _MemoryIndex, vectors: list[list[float]], vector_ids: list[int]) -> None:
        """按稳定 ID 写入向量，模拟 IDMap2 追加行为。"""
        index.vectors.update({vector_id: vector for vector_id, vector in zip(vector_ids, vectors, strict=True)})  # 保持调用方提供的稳定 ID。

    def search(self, index: _MemoryIndex, query: list[float], limit: int) -> tuple[list[float], list[int]]:
        """按内积降序返回结果，不足数量时使用 FAISS 一致的 -1 填充。"""
        matches = sorted(((sum(query_value * value for query_value, value in zip(query, vector, strict=True)), vector_id) for vector_id, vector in index.vectors.items()), reverse=True)  # 计算并排序精确内积分数。
        scores = [score for score, _ in matches[:limit]]  # 提取请求数量内的有序分数。
        vector_ids = [vector_id for _, vector_id in matches[:limit]]  # 提取与分数对应的稳定 ID。
        return scores + [0.0] * (limit - len(scores)), vector_ids + [-1] * (limit - len(vector_ids))  # 模拟 FAISS 空槽填充语义。

    def dimension(self, index: _MemoryIndex) -> int:
        """返回内存索引的固定维度。"""
        return index.dimension  # 对齐 FAISS d 属性行为。

    def count(self, index: _MemoryIndex) -> int:
        """返回当前索引总向量数。"""
        return len(index.vectors)  # 对齐 FAISS ntotal 行为。


def test_add_saves_atomically_and_search_filters_inactive_vectors(tmp_path: Path) -> None:
    """写入应发布正式文件，查询应跳过 SQLite 标记为无效的候选。"""
    backend = _MemoryFaissBackend()  # 创建无需二进制依赖的测试后端。
    index_path = tmp_path / "library.index"  # 使用测试专属索引路径避免业务数据写入。
    manager = FaissIndexManager("library", index_path, backend=backend)  # 创建未加载索引的管理器。

    count = manager.add([[1.0, 0.0], [0.8, 0.2]], [11, 12])  # 写入两个已由 SQLite 分配的向量 ID。
    hits = manager.search([1.0, 0.0], top_k=2, active_vector_ids={12})  # 过滤最高分但已逻辑失效的 ID 11。

    assert count == 2  # 验证索引总数与成功写入数量一致。
    assert index_path.exists()  # 验证临时文件已通过原子替换发布为正式索引。
    assert not index_path.with_name("library.index.tmp").exists()  # 验证发布后不保留临时索引文件。
    assert [hit.vector_id for hit in hits] == [12]  # 验证扩展候选经过活动映射过滤后仍能补足有效结果。
    assert manager.dimension() == 2 and manager.count() == 2  # 验证管理器暴露的索引统计正确。


def test_new_manager_loads_published_index_from_backend(tmp_path: Path) -> None:
    """新的进程内管理器应能加载已完整发布的索引并返回稳定结果。"""
    backend = _MemoryFaissBackend()  # 让两个管理器共享模拟磁盘后端。
    index_path = tmp_path / "global.index"  # 使用独立测试索引文件。
    first_manager = FaissIndexManager("global_papers", index_path, backend=backend)  # 创建首次写入管理器。
    first_manager.add([[0.0, 1.0]], [21])  # 发布一条向量到模拟索引文件。
    second_manager = FaissIndexManager("global_papers", index_path, backend=backend)  # 模拟新进程启动后的全新管理器。

    hits = second_manager.search([0.0, 1.0], top_k=1)  # 触发从正式文件按需加载。

    assert [hit.vector_id for hit in hits] == [21]  # 验证新管理器可读取已发布索引。


def test_add_rejects_dimension_changes_for_existing_index(tmp_path: Path) -> None:
    """不同维度的模型向量不得混入同一个 FAISS 索引文件。"""
    manager = FaissIndexManager("library", tmp_path / "library.index", backend=_MemoryFaissBackend())  # 使用内存后端创建测试管理器。
    manager.add([[1.0, 0.0]], [1])  # 首次写入确定索引维度为二。

    with pytest.raises(FaissIndexError, match="维度不匹配"):  # 断言返回稳定重建提示边界。
        manager.add([[1.0, 0.0, 0.0]], [2])  # 尝试混入三维向量。


def test_search_rejects_invalid_input_and_returns_empty_for_missing_index(tmp_path: Path) -> None:
    """不存在索引应返回空结果，无效查询向量应在进入后端前被拒绝。"""
    manager = FaissIndexManager("library", tmp_path / "missing.index", backend=_MemoryFaissBackend())  # 创建尚未写入的管理器。

    assert manager.search([1.0, 0.0], top_k=1) == []  # 验证首次检索没有索引文件时不报错。
    with pytest.raises(FaissIndexError, match="查询向量无效"):  # 断言空查询向量使用稳定错误边界。
        manager.search([], top_k=1)  # 提交没有语义维度的无效输入。
