"""编排用户显式提供的准备数据集金标的完全离线导入。"""

import json  # 将转换后的 GoldQuery 编码为 UTF-8 JSONL。
import os  # 刷新文件内容并原子发布最终输出文件。
from pathlib import Path  # 读取和写入用户显式指定的本地路径。
from tempfile import NamedTemporaryFile  # 在同目录创建可安全发布的临时 JSONL 文件。

from evaluation.adapters.prepared_dataset import convert_prepared_dataset_records  # 复用不接触网络的金标转换规则。
from evaluation.contracts.dataset import PreparedDatasetGoldRecord  # 解析准备数据集金标输入。
from evaluation.contracts.gold import GoldQuery  # 返回现有评测运行器可读取的统一金标。
from evaluation.runners.fixture import load_jsonl  # 复用带 UTF-8、行号和 Pydantic 校验的 JSONL 加载器。


def import_prepared_dataset_gold(
    input_path: Path,
    *,
    dataset_id: str,
    split: str,
    output_path: Path,
) -> list[GoldQuery]:
    """读取本地准备数据，转换为 GoldQuery 并写入一个新的 JSONL 文件。

    此函数不读取 `.env`、不访问网络、不下载数据集，也不运行任何模型；输出已存在时会在
    读取输入前失败，避免用户将一次人工准备结果误覆盖为不同版本。
    """
    if output_path.exists():  # 先保护已有导入结果，避免不必要读取和转换。
        raise FileExistsError(f"数据集金标输出已存在: {output_path}")  # 禁止静默覆盖可复核输入。
    records = load_jsonl(input_path, PreparedDatasetGoldRecord)  # 只读加载用户已准备的本地 JSONL。
    gold_queries = convert_prepared_dataset_records(records, dataset_id=dataset_id, split=split)  # 在内存中完成零网络转换和质量校验。
    if not gold_queries:  # 空数据集无法形成稳定评测分母。
        raise ValueError("准备数据集金标不包含有效查询")  # 防止生成看似成功的空输出文件。
    write_gold_queries(gold_queries, output_path)  # 将所有已校验记录安全发布为新的 JSONL。
    return gold_queries  # 返回写入内容供 CLI 输出不含查询正文的计数摘要。


def write_gold_queries(gold_queries: list[GoldQuery], output_path: Path) -> None:
    """通过同目录临时文件将 GoldQuery 列表原子写入尚不存在的 JSONL。"""
    if output_path.exists():  # 缩小导入期间目标被创建时的覆盖窗口。
        raise FileExistsError(f"数据集金标输出已存在: {output_path}")  # 始终保留已有用户文件。
    output_path.parent.mkdir(parents=True, exist_ok=True)  # 仅创建用户显式指定输出路径的父目录。
    serialized = "".join(json.dumps(query.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n" for query in gold_queries)  # 生成每条一行的稳定 UTF-8 JSONL 内容。
    temporary_path: Path | None = None  # 保存临时文件路径以便异常时回收。
    try:  # 任何写入或发布失败都不得留下不完整目标文件。
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp", delete=False) as stream:  # 在同一文件系统创建临时文件。
            temporary_path = Path(stream.name)  # 保存关闭后可用于发布的临时文件路径。
            stream.write(serialized)  # 一次写入完整、已校验的金标内容。
            stream.flush()  # 将 Python 文本缓冲刷新到底层文件描述符。
            os.fsync(stream.fileno())  # 请求操作系统在最终发布前完成落盘。
        if output_path.exists():  # 替换前再次检查，避免覆盖并发创建的用户输出。
            raise FileExistsError(f"数据集金标输出已存在: {output_path}")  # 保留用户先创建的目标。
        os.replace(temporary_path, output_path)  # 在同目录原子发布完整 JSONL 文件。
        temporary_path = None  # 标记临时文件已经成为最终输出，无需清理。
    finally:
        if temporary_path is not None and temporary_path.exists():  # 仅清理尚未发布的临时文件。
            temporary_path.unlink()  # 避免导入失败在用户目录遗留碎片。
