"""封装 FAISS 内积索引的添加、查询、原子保存和逻辑失效过滤。"""

import os  # 使用同目录原子替换发布完整索引文件。
from dataclasses import dataclass  # 使用稳定值对象表达索引查询命中。
from math import isfinite  # 在写入索引前拒绝 NaN 和无穷向量值。
from pathlib import Path  # 表示受配置层控制的索引文件路径。
from threading import RLock  # 在单进程内串行化索引读写与保存。
from typing import Protocol  # 定义可由离线测试替换的 FAISS 后端边界。

from backend.app.core.logging import logger  # 记录索引数量、维度和耗时以便诊断。


class FaissIndexError(RuntimeError):
    """表示 FAISS 依赖、索引兼容性、文件保存或输入向量错误。"""


@dataclass(frozen=True)
class IndexSearchHit:
    """保存一条经过可选活动映射过滤的向量检索命中。"""

    vector_id: int  # 保存 SQLite 可映射回 PaperRecord 的稳定整数 ID。
    score: float  # 保存归一化向量内积对应的余弦相似度。


class FaissIndexBackend(Protocol):
    """约束 IndexManager 使用的最小 FAISS 操作，隔离 C++ 与 NumPy 细节。"""

    def create(self, dimension: int) -> object:
        """创建 IndexIDMap2(IndexFlatIP) 空索引。"""
        ...  # Protocol 仅声明存储操作边界。

    def read(self, path: Path) -> object:
        """从磁盘读取索引对象。"""
        ...  # 由生产或测试后端实现。

    def write(self, index: object, path: Path) -> None:
        """将索引对象写入指定路径。"""
        ...  # 由调用方负责临时路径和原子替换。

    def add_with_ids(self, index: object, vectors: list[list[float]], vector_ids: list[int]) -> None:
        """向索引追加与 SQLite 一致的稳定整数 ID。"""
        ...  # 不允许后端自行生成不受控 ID。

    def search(self, index: object, query: list[float], limit: int) -> tuple[list[float], list[int]]:
        """按内积降序返回单个查询的分数和向量 ID。"""
        ...  # 返回标准 Python 容器以隔离 NumPy 数组。

    def dimension(self, index: object) -> int:
        """返回索引固定向量维度。"""
        ...  # 用于写入和查询前的兼容性校验。

    def count(self, index: object) -> int:
        """返回当前索引包含的总向量数。"""
        ...  # 用于原子保存后的完整性验证。


class FaissCpuBackend:
    """在真正读写索引时才导入 faiss-cpu 与 NumPy 的生产后端。"""

    def create(self, dimension: int) -> object:
        """创建精确内积检索的 IndexIDMap2(IndexFlatIP) 索引。"""
        faiss, _ = self._load_dependencies()  # 延迟导入避免服务启动强制加载 C++ 扩展。
        return faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))  # 使用规划指定的可重建精确索引结构。

    def read(self, path: Path) -> object:
        """读取已完整发布的 FAISS 索引文件。"""
        faiss, _ = self._load_dependencies()  # 在实际读取时才加载可选依赖。
        return faiss.read_index(str(path))  # FAISS 负责校验索引二进制格式。

    def write(self, index: object, path: Path) -> None:
        """将索引写入调用方给定的临时文件路径。"""
        faiss, _ = self._load_dependencies()  # 在实际持久化时才加载可选依赖。
        faiss.write_index(index, str(path))  # 仅写临时文件，正式替换由管理器控制。

    def add_with_ids(self, index: object, vectors: list[list[float]], vector_ids: list[int]) -> None:
        """将 Python 向量与 SQLite 分配的整数 ID 转换为 FAISS 所需数组。"""
        _, numpy = self._load_dependencies()  # 延迟加载 NumPy 并避免业务层依赖数组类型。
        vector_array = numpy.asarray(vectors, dtype="float32")  # FAISS CPU 接口要求连续 float32 向量数组。
        id_array = numpy.asarray(vector_ids, dtype="int64")  # FAISS IDMap2 使用 64 位整数 ID。
        index.add_with_ids(vector_array, id_array)  # 由 FAISS 追加向量并保留外部稳定 ID。

    def search(self, index: object, query: list[float], limit: int) -> tuple[list[float], list[int]]:
        """执行单向量内积检索并转为稳定 Python 列表。"""
        _, numpy = self._load_dependencies()  # 延迟加载 NumPy 以构造二维查询数组。
        scores, vector_ids = index.search(numpy.asarray([query], dtype="float32"), limit)  # 使用 FAISS 返回一行 Top-K 分数和 ID。
        return [float(score) for score in scores[0]], [int(vector_id) for vector_id in vector_ids[0]]  # 隔离 NumPy 标量和 -1 空槽标记。

    def dimension(self, index: object) -> int:
        """读取 FAISS 索引的固定向量维度。"""
        return int(index.d)  # FAISS d 属性表示索引维度。

    def count(self, index: object) -> int:
        """读取 FAISS 索引当前的向量总数。"""
        return int(index.ntotal)  # FAISS ntotal 包含逻辑失效但尚未重建的向量。

    @staticmethod
    def _load_dependencies() -> tuple[object, object]:
        """在运行期按需导入 faiss-cpu 与 NumPy，并隐藏导入细节。"""
        try:  # 将可选 C++ 扩展导入限制在仓储适配层。
            import faiss  # faiss-cpu 安装后提供的 Python 模块名称。
            import numpy  # FAISS Python 接口要求的数组转换依赖。
        except Exception as error:  # 依赖缺失或二进制不兼容时返回安全错误。
            raise FaissIndexError("FAISS 索引依赖不可用") from error  # 不向调用方暴露二进制路径或环境细节。
        return faiss, numpy  # 返回仅供当前适配器方法使用的第三方模块。


class FaissIndexManager:
    """管理单个可重建 FAISS 文件，提供线程安全写入、保存和过滤后的查询。

    参数：
        index_name：索引逻辑名称，例如 global_papers 或 library。
        index_path：索引文件路径，应由配置或组合根显式提供。
        backend：可选 FAISS 后端，测试可注入内存替身。
    """

    def __init__(self, index_name: str, index_path: Path, backend: FaissIndexBackend | None = None) -> None:
        """保存索引身份和路径，不加载依赖、不读写文件。"""
        if not index_name.strip():  # 无逻辑名称无法与 SQLite index_metadata 对应。
            raise ValueError("index_name 不能为空")  # 提前拒绝无法追踪的索引。
        self._index_name = index_name.strip()  # 保存规范化索引名称。
        self._index_path = index_path  # 保存由调用方配置的正式索引路径。
        self._backend = backend or FaissCpuBackend()  # 默认使用懒加载生产后端。
        self._index: object | None = None  # 延迟加载或创建实际索引对象。
        self._lock = RLock()  # 串行化同进程读写和原子发布。

    @property
    def index_name(self) -> str:
        """返回用于 SQLite 元数据和日志关联的索引名称。"""
        return self._index_name  # 不暴露可包含本地目录的文件路径。

    def add(self, vectors: list[list[float]], vector_ids: list[int]) -> int:
        """追加已完成 pending 预写的向量，并以临时文件原子发布新索引。

        返回：
            int：原子保存后的索引向量总数。
        异常：
            FaissIndexError：依赖、维度、ID、向量值或保存校验失败时抛出。
        """
        try:  # 将输入校验失败统一映射为索引仓储错误。
            dimension = _validate_vector_batch(vectors, vector_ids)  # 在调用 FAISS 前校验输入形状和数值。
        except ValueError as error:  # 不允许调用方看到内部校验细节或第三方类型。
            raise FaissIndexError("FAISS 写入向量无效") from error  # 提供稳定的索引写入边界。
        with self._lock:  # 防止并发 add 和 save 互相覆盖索引文件。
            index = self._get_or_create_index(dimension)  # 首次写入时根据向量维度创建规划指定索引。
            if self._backend.dimension(index) != dimension:  # 禁止模型或文本版本切换后混入不同维度。
                raise FaissIndexError("FAISS 索引维度不匹配")  # 要求调用方创建新索引或重建旧索引。
            try:  # 将第三方写入失败映射为稳定仓储错误。
                self._backend.add_with_ids(index, vectors, vector_ids)  # 使用 SQLite 分配的稳定 64 位 ID 写入索引。
                self._save_atomically(index)  # 仅在完整写入和校验后发布新索引文件。
            except FaissIndexError:  # 保留已净化错误，避免重复包装。
                self._index = None  # 保存失败后丢弃可能已部分变更的内存索引，后续从正式文件重新加载。
                raise  # 交由调用方标记 pending 映射失败。
            except Exception as error:  # FAISS 或文件系统异常不应泄露给业务层。
                self._index = None  # 第三方写入失败同样不得让进程内索引领先于磁盘文件。
                logger.exception("FAISS 索引写入失败：索引=%s，向量数=%d", self._index_name, len(vectors))  # 记录数量而不记录向量内容。
                raise FaissIndexError("FAISS 索引写入失败") from error  # 返回稳定错误边界。
            count = self._backend.count(index)  # 读取原子保存后的当前索引数量。
            logger.info("FAISS 索引写入完成：索引=%s，新增=%d，总数=%d，维度=%d", self._index_name, len(vectors), count, dimension)  # 记录重建和容量规划需要的统计。
            return count  # 返回供 SQLite index_metadata 更新的总量。

    def search(self, query_vector: list[float], top_k: int, active_vector_ids: set[int] | None = None, candidate_multiplier: int = 3) -> list[IndexSearchHit]:
        """按内积查询并过滤逻辑失效向量，返回最多 top_k 条稳定命中。"""
        if top_k < 1:  # 零目标结果没有检索语义。
            raise ValueError("top_k 必须大于零")  # 在调用 FAISS 前暴露无效请求。
        if candidate_multiplier < 1:  # 乘数小于一无法为失效过滤预留候选。
            raise ValueError("candidate_multiplier 必须大于零")  # 保证补足候选策略存在。
        try:  # 将查询向量校验失败统一映射为索引仓储错误。
            _validate_single_vector(query_vector)  # 拒绝维度为零、NaN 或无穷查询向量。
        except ValueError as error:  # 不向调用方暴露内部数值校验细节。
            raise FaissIndexError("FAISS 查询向量无效") from error  # 返回稳定的检索输入错误。
        with self._lock:  # 防止查询读取到正在替换中的内存索引状态。
            index = self._load_if_needed()  # 首次查询时仅加载已完整发布的索引文件。
            if index is None:  # 空索引文件不存在时返回稳定空结果。
                return []  # 调用方无需将首次使用视为错误。
            if self._backend.dimension(index) != len(query_vector):  # 禁止不同 BGE 模型维度直接查询旧索引。
                raise FaissIndexError("FAISS 查询向量维度不匹配")  # 要求调用方重建或选择匹配索引。
            candidate_limit = min(self._backend.count(index), top_k * candidate_multiplier)  # 扩大 FAISS 候选以抵消 SQLite 逻辑失效过滤。
            if candidate_limit < 1:  # 防御存在空索引对象的边界。
                return []  # 返回稳定空集合。
            try:  # 将第三方搜索错误映射为稳定仓储错误。
                scores, vector_ids = self._backend.search(index, query_vector, candidate_limit)  # 读取候选分数和外部 ID。
            except Exception as error:  # FAISS 二进制或数组错误不应泄露到 API。
                logger.exception("FAISS 索引查询失败：索引=%s，候选数=%d", self._index_name, candidate_limit)  # 仅记录安全统计。
                raise FaissIndexError("FAISS 索引查询失败") from error  # 返回稳定检索失败边界。
        hits: list[IndexSearchHit] = []  # 在锁外过滤，缩短并发读取的临界区。
        for score, vector_id in zip(scores, vector_ids, strict=True):  # 保持 FAISS 已排序候选顺序。
            if vector_id < 0:  # FAISS 使用 -1 填充不足候选的空槽。
                continue  # 跳过无映射的占位命中。
            if active_vector_ids is not None and vector_id not in active_vector_ids:  # SQLite 映射负责过滤 inactive、pending 和 failed 向量。
                continue  # 不让逻辑失效论文重新出现在结果中。
            hits.append(IndexSearchHit(vector_id=vector_id, score=score))  # 保存可映射回 PaperRecord 的安全命中。
            if len(hits) == top_k:  # 目标数量已满足，无需继续遍历扩展候选。
                break  # 保持 Top-K 延迟和内存开销可控。
        logger.info("FAISS 索引查询完成：索引=%s，目标=%d，候选=%d，返回=%d", self._index_name, top_k, candidate_limit, len(hits))  # 记录失效过滤后的返回数量。
        return hits  # 返回按内积降序排列的有效命中。

    def dimension(self) -> int | None:
        """返回当前已加载或已发布索引维度；不存在时返回空值。"""
        with self._lock:  # 防止读取时与索引替换竞争。
            index = self._load_if_needed()  # 按需读取完整索引文件。
            return self._backend.dimension(index) if index is not None else None  # 空索引尚无维度。

    def count(self) -> int:
        """返回当前索引总向量数，逻辑失效向量仍包含在内以支持定期重建。"""
        with self._lock:  # 防止读取时与索引替换竞争。
            index = self._load_if_needed()  # 按需读取完整索引文件。
            return self._backend.count(index) if index is not None else 0  # 不存在索引文件时返回零。

    def _get_or_create_index(self, dimension: int) -> object:
        """返回已有索引，或在首次 add 时创建指定维度的空索引。"""
        index = self._load_if_needed()  # 优先复用已加载或磁盘发布的索引。
        if index is None:  # 首次写入尚不存在索引文件。
            self._index = self._backend.create(dimension)  # 创建 IndexIDMap2(IndexFlatIP) 精确内积索引。
        return self._index  # self._index 已在两条路径中得到有效对象。

    def _load_if_needed(self) -> object | None:
        """仅在首次访问且正式文件存在时加载索引，避免构造阶段产生 I/O。"""
        if self._index is not None:  # 已加载或刚创建的索引无需重复读取。
            return self._index  # 复用进程内索引对象。
        if not self._index_path.exists():  # 未建立索引是首次使用的正常状态。
            return None  # 由 add 或 search 分别创建或返回空结果。
        try:  # 将 FAISS 读取失败映射为稳定仓储错误。
            self._index = self._backend.read(self._index_path)  # 仅加载原子替换完成后的正式文件。
        except Exception as error:  # 损坏或不兼容索引不得被静默忽略。
            logger.exception("FAISS 索引加载失败：索引=%s", self._index_name)  # 记录索引名称但不输出本地绝对路径。
            raise FaissIndexError("FAISS 索引加载失败") from error  # 要求调用方执行显式重建。
        return self._index  # 返回已加载索引。

    def _save_atomically(self, index: object) -> None:
        """写入临时文件、复读校验数量和维度后再原子替换正式索引。"""
        self._index_path.parent.mkdir(parents=True, exist_ok=True)  # 确保由配置指定的数据目录存在。
        temporary_path = self._index_path.with_name(f"{self._index_path.name}.tmp")  # 临时文件与正式文件保持同目录以支持原子替换。
        try:  # 清理旧临时文件并执行完整发布流程。
            temporary_path.unlink(missing_ok=True)  # 仅删除当前索引受控路径的残留临时文件。
            self._backend.write(index, temporary_path)  # 先写入不会被查询加载的临时索引。
            verified_index = self._backend.read(temporary_path)  # 复读临时文件验证写入完整性。
            if self._backend.count(verified_index) != self._backend.count(index) or self._backend.dimension(verified_index) != self._backend.dimension(index):  # 拒绝数量或维度不一致的半成品索引。
                raise FaissIndexError("FAISS 临时索引校验失败")  # 不允许替换仍可用的旧正式文件。
            os.replace(temporary_path, self._index_path)  # 使用同目录原子替换发布完整索引。
        except FaissIndexError:  # 保留已净化错误供调用方处理 pending 状态。
            raise  # 不重复包装。
        except Exception as error:  # 文件系统或第三方写入错误必须隐藏内部路径。
            raise FaissIndexError("FAISS 索引保存失败") from error  # 向业务层提供安全错误。
        finally:  # 无论成功或失败都尽量清理受控临时文件。
            temporary_path.unlink(missing_ok=True)  # 避免残留文件干扰下次原子保存。


def _validate_vector_batch(vectors: list[list[float]], vector_ids: list[int]) -> int:
    """校验批量向量、稳定 ID 和统一维度，返回当前批次维度。"""
    if not vectors or len(vectors) != len(vector_ids):  # 空批量或数量不一致无法建立可靠映射。
        raise ValueError("向量与 vector_id 必须为等长非空列表")  # 拒绝可能错配论文的写入请求。
    if len(set(vector_ids)) != len(vector_ids) or any(vector_id < 1 for vector_id in vector_ids):  # SQLite 自动分配 ID 必须正且批内唯一。
        raise ValueError("vector_id 必须为唯一正整数")  # 防止 IDMap2 覆盖或无效映射。
    dimension = len(vectors[0])  # 使用首向量作为批次维度基准。
    if dimension < 1:  # 零维向量不能创建 FAISS 索引。
        raise ValueError("向量维度必须大于零")  # 阻止无意义索引写入。
    for vector in vectors:  # 验证每条向量可安全转换为 FAISS float32 输入。
        if len(vector) != dimension or not all(isfinite(float(value)) for value in vector):  # 拒绝混合维度、NaN 和无穷值。
            raise ValueError("向量维度不一致或包含非有限值")  # 防止 C++ 扩展接收异常数据。
    return dimension  # 返回经验证的固定索引维度。


def _validate_single_vector(vector: list[float]) -> None:
    """校验单条查询向量在维度与数值上可安全进入 FAISS。"""
    if not vector or not all(isfinite(float(value)) for value in vector):  # 空、NaN 和无穷查询没有可解释检索语义。
        raise ValueError("查询向量不能为空且必须为有限值")  # 在调用 C++ 扩展前返回稳定输入错误。
