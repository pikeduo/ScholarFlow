"""将用户本地准备的数据集金标稳定转换为 ScholarFlow GoldQuery。"""

from collections.abc import Sequence  # 接收已按输入顺序加载的准备数据记录。

from evaluation.contracts.dataset import PreparedDatasetGoldRecord  # 读取严格的中间数据集输入契约。
from evaluation.contracts.gold import GoldQuery  # 输出现有离线评测可直接消费的统一金标契约。
from evaluation.metrics.identifiers import deduplicate_papers  # 复用统一论文身份规则验证来源金标重复。


IMPORT_SCHEMA_VERSION = "prepared-dataset-gold-v1"  # 固定当前用户准备文件与输出元数据的转换版本。


def convert_prepared_dataset_records(
    records: Sequence[PreparedDatasetGoldRecord],
    *,
    dataset_id: str,
    split: str,
) -> list[GoldQuery]:
    """将本地准备记录转换为带稳定命名空间和审计元数据的 GoldQuery。

    参数：
        records：已通过结构校验的本地准备数据集记录。
        dataset_id：用户明确指定的数据集名称，不从目录或文件名推断。
        split：用户明确指定的数据集切分名称，不从记录随机推断。
    返回：
        list[GoldQuery]：保持输入顺序且可直接传给现有离线评测的金标查询。
    异常：
        ValueError：数据集标签为空、原始查询重复或单条金标存在重复论文时抛出。
    """
    normalized_dataset_id = _normalize_label(dataset_id, "dataset_id")  # 规范化但不改写用户选择的数据集标识。
    normalized_split = _normalize_label(split, "split")  # 规范化但不猜测 train、dev 或 test。
    source_query_ids: set[str] = set()  # 记录原始查询键以避免生成含糊的命名空间标识。
    gold_queries: list[GoldQuery] = []  # 保持准备文件顺序输出，方便人工复核行号。
    for record in records:  # 逐条转换并在写文件前完成所有质量校验。
        source_query_id = record.source_query_id.strip()  # 移除用户输入两侧无语义空白。
        if source_query_id in source_query_ids:  # 同一数据集切分的原始查询键必须唯一。
            raise ValueError(f"准备数据集金标存在重复 source_query_id: {source_query_id}")  # 拒绝不确定的评分分母。
        source_query_ids.add(source_query_id)  # 锁定已使用的原始查询键。
        _, duplicate_count = deduplicate_papers(record.relevant_papers)  # 使用与评分一致的身份优先级检查金标论文。
        if duplicate_count:  # 导入器不静默丢弃公开数据集的人工标注。
            raise ValueError(f"准备数据集金标 {source_query_id} 包含 {duplicate_count} 篇重复相关论文")  # 要求用户先确认来源数据。
        metadata = {  # 构造由导入器负责的可审计元数据副本。
            "dataset": normalized_dataset_id,  # 冻结用户显式选择的数据集名称。
            "split": normalized_split,  # 冻结用户显式选择的数据集切分。
            "source_query_id": source_query_id,  # 保留可回溯到原始数据集的查询键。
            "import_schema_version": IMPORT_SCHEMA_VERSION,  # 记录转换规则版本以支持后续演进。
            **record.metadata,  # 保留已通过保留字段校验的来源附加标量。
        }
        gold_queries.append(GoldQuery(query_id=f"{normalized_dataset_id}:{normalized_split}:{source_query_id}", query=record.query, relevant_papers=list(record.relevant_papers), metadata=metadata))  # 生成避免不同数据集同名查询冲突的稳定标识。
    return gold_queries  # 返回可由现有 fixture 评分器直接消费的统一金标。


def _normalize_label(value: str, label_name: str) -> str:
    """校验数据集或切分标签非空，并保留其可读显示形式。"""
    normalized_value = value.strip()  # 去除无语义前后空白。
    if not normalized_value:  # 空标签不能形成稳定金标命名空间。
        raise ValueError(f"{label_name} 不能为空")  # 在读取或写入大量数据前返回明确错误。
    if ":" in normalized_value:  # 冒号会破坏 ``dataset:split:source_query_id`` 可解析命名空间。
        raise ValueError(f"{label_name} 不能包含冒号")  # 要求用户使用不含分隔符的显示名称。
    return normalized_value  # 返回通过边界校验的标签。
