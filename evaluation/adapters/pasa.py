"""将已确认字段版本的 PaSa 原始记录转换为统一评测金标。"""

from __future__ import annotations  # 延迟求值类型标注，使独立离线模块可在较早解释器中安全导入。

from typing import Sequence  # 接收保持原始 JSONL 顺序且兼容现有解释器的 PaSa 记录集合。

from evaluation.adapters.prepared_dataset import convert_prepared_dataset_records  # 复用通用命名空间、去重和审计元数据规则。
from evaluation.contracts.common import EvaluationPaper  # 构造标题与 arXiv 标识组成的金标论文。
from evaluation.contracts.dataset import PreparedDatasetGoldRecord  # 先映射为通用本地准备数据集边界。
from evaluation.contracts.gold import GoldQuery  # 返回现有离线评测器可直接消费的统一金标。
from evaluation.contracts.pasa import PasaRawQuery  # 读取已确认字段版本的 PaSa 原始记录。


PASA_AUTOSCHOLARQUERY_SCHEMA_VERSION = "pasa-autoscholarquery-jsonl-v1"  # 冻结当前经本地样例确认的 PaSa 字段映射版本。


def convert_pasa_records(records: Sequence[PasaRawQuery], *, split: str) -> list[GoldQuery]:
    """将 PaSa 原始记录转换为带 arXiv 身份和来源元数据的 GoldQuery。

    参数：
        records：按原始 JSONL 顺序加载且已通过 PaSa 契约校验的记录。
        split：评测切分名称；当前原生命令仅允许已确认格式的 ``auto-dev``。
    返回：
        list[GoldQuery]：可由既有离线评分器直接读取的统一金标。
    异常：
        ValueError：原始查询键重复、单条金标论文重复或切分标签非法时抛出。
    """
    prepared_records = [_to_prepared_record(record) for record in records]  # 先完整映射，避免在导入层散落通用转换逻辑。
    return convert_prepared_dataset_records(prepared_records, dataset_id="pasa", split=split)  # 复用统一命名空间、重复论文和重复查询校验。


def _to_prepared_record(record: PasaRawQuery) -> PreparedDatasetGoldRecord:
    """将一条已校验 PaSa 记录映射为通用准备数据集记录。"""
    arxiv_ids = record.answer_arxiv_id or [None] * len(record.answer)  # 整体缺失 arXiv 标识时保留标题金标，而非访问论文数据库补全。
    relevant_papers = [
        EvaluationPaper(title=title, arxiv_id=arxiv_id)  # 严格依据同索引 PaSa 标题和 arXiv 标识构造论文身份。
        for title, arxiv_id in zip(record.answer, arxiv_ids, strict=True)  # 长度已由 PaSa 契约校验，禁止静默截断。
    ]
    source_metadata = {
        f"pasa_source_{key}": value  # 为 PaSa 来源元数据加稳定命名空间，避免与通用审计字段冲突。
        for key, value in record.source_meta.items()
    }
    metadata = {
        "pasa_schema_version": PASA_AUTOSCHOLARQUERY_SCHEMA_VERSION,  # 冻结当前字段映射版本供报告和重跑审计。
        **source_metadata,  # 仅保留可写入 JSON 的来源标量元数据。
    }
    return PreparedDatasetGoldRecord(  # 交由通用转换器负责命名空间、重复查询和论文身份审计。
        source_query_id=record.qid,
        query=record.question,
        relevant_papers=relevant_papers,
        metadata=metadata,
    )
