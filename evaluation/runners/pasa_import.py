"""编排本地 PaSa JSONL 到统一 GoldQuery JSONL 的完全离线转换。"""

from pathlib import Path  # 接收用户明确指定的原始输入和新输出路径。

from evaluation.adapters.pasa import convert_pasa_records  # 复用已确认 PaSa 字段版本的纯内存映射。
from evaluation.contracts.gold import GoldQuery  # 返回现有离线评分器兼容的金标记录。
from evaluation.contracts.pasa import PasaRawQuery  # 解析 PaSa 原始 JSONL 每行契约。
from evaluation.runners.dataset_import import write_gold_queries  # 复用拒绝覆盖的原子 GoldQuery JSONL 写入器。
from evaluation.runners.fixture import load_jsonl  # 复用 UTF-8、行号和 Pydantic JSONL 加载边界。


def import_pasa_gold(input_path: Path, *, split: str, output_path: Path) -> list[GoldQuery]:
    """读取用户已下载的 PaSa JSONL 并输出一份新的统一 GoldQuery JSONL。"""
    if output_path.exists():  # 在读取可能较大的原始文件前保护已有人工审阅结果。
        raise FileExistsError(f"PaSa 金标输出已存在: {output_path}")  # 禁止静默覆盖已有评测输入。
    records = load_jsonl(input_path, PasaRawQuery)  # 只读解析用户明确提供的本地 PaSa 原始文件。
    gold_queries = convert_pasa_records(records, split=split)  # 不访问论文 API、LLM、模型或网络地完成转换。
    if not gold_queries:  # 空输入不能形成稳定评测分母。
        raise ValueError("PaSa 原始文件不包含有效查询")  # 防止生成误导性的空 GoldQuery 文件。
    write_gold_queries(gold_queries, output_path)  # 通过同目录临时文件安全发布新的统一金标。
    return gold_queries  # 返回实际写入集合供 CLI 输出无敏感摘要。
