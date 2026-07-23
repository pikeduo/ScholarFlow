"""测试用户本地准备数据集金标的零网络导入边界。"""

import json  # 构造临时 JSONL 输入并检查写出结果。
from pathlib import Path  # 使用 pytest 临时目录隔离输入和输出文件。

import pytest  # 验证重复、保留字段和已有输出拒绝路径。

from evaluation.adapters.prepared_dataset import IMPORT_SCHEMA_VERSION, convert_prepared_dataset_records  # 直接验证纯内存转换规则。
from evaluation.cli import main  # 验证默认离线 CLI 命令。
from evaluation.contracts.dataset import PreparedDatasetGoldRecord  # 构造已准备数据集金标记录。
from evaluation.runners.dataset_import import import_prepared_dataset_gold  # 执行本地文件导入闭环。
from evaluation.runners.fixture import load_jsonl  # 使用现有 GoldQuery 加载器验证输出兼容性。
from evaluation.contracts.gold import GoldQuery  # 解析导入后的统一金标。


def _record(source_query_id: str = "source-001", *, papers: list[dict[str, object]] | None = None, metadata: dict[str, object] | None = None) -> PreparedDatasetGoldRecord:
    """构造一条不依赖外部数据集或网络的准备记录。"""
    return PreparedDatasetGoldRecord(source_query_id=source_query_id, query="graph neural networks for survey", relevant_papers=papers or [{"doi": "10.1000/example", "title": "Example Paper", "year": 2024, "authors": ["Alice"]}], metadata=metadata or {"source_license": "test-only"})  # 返回具备强论文身份和可归档标量的合成记录。


def test_conversion_namespaces_queries_and_preserves_audit_metadata() -> None:
    """导入器应生成稳定命名空间键并保留来源与转换版本元数据。"""
    converted = convert_prepared_dataset_records([_record()], dataset_id="pasa", split="dev-small")  # 执行纯内存、零网络转换。
    assert len(converted) == 1  # 一条准备记录应生成一条 GoldQuery。
    assert converted[0].query_id == "pasa:dev-small:source-001"  # 输出键必须包含数据集、切分和原始查询键。
    assert converted[0].metadata == {"dataset": "pasa", "split": "dev-small", "source_query_id": "source-001", "import_schema_version": IMPORT_SCHEMA_VERSION, "source_license": "test-only"}  # 验证审计字段和来源标量均被冻结。


def test_conversion_rejects_duplicate_source_queries_and_gold_papers() -> None:
    """输入重复不得被静默合并或丢弃，以保持评测分母可审计。"""
    with pytest.raises(ValueError, match="重复 source_query_id"):  # 同一数据集切分内查询键必须唯一。
        convert_prepared_dataset_records([_record(), _record()], dataset_id="pasa", split="dev")
    duplicate_papers = [{"doi": "10.1000/same", "title": "First"}, {"doi": "DOI:10.1000/SAME", "title": "Duplicate"}]  # 构造统一身份规则可识别的重复论文。
    with pytest.raises(ValueError, match="重复相关论文"):  # 金标集合不能因来源重复而扩大分母。
        convert_prepared_dataset_records([_record(papers=duplicate_papers)], dataset_id="pasa", split="dev")


def test_input_contract_rejects_reserved_metadata_keys() -> None:
    """来源记录不得覆盖数据集、切分或转换版本等导入器审计字段。"""
    with pytest.raises(ValueError, match="metadata 不能包含保留字段: dataset"):  # 验证保留字段错误可定位。
        _record(metadata={"dataset": "untrusted"})
    with pytest.raises(ValueError, match="source_query_id 不能只包含空白"):  # 验证人眼不可见的空白查询键也被拒绝。
        _record(source_query_id="   ")


def test_file_import_writes_goldquery_jsonl_and_refuses_existing_output(tmp_path: Path) -> None:
    """导入器应输出现有评分器可读取的 JSONL，并在读取前保护已有目标。"""
    input_path = tmp_path / "prepared.jsonl"  # 指定用户手动准备的本地输入。
    input_path.write_text(json.dumps(_record().model_dump(mode="json"), ensure_ascii=False) + "\n", encoding="utf-8")  # 写入一条完全合成的 UTF-8 JSONL。
    output_path = tmp_path / "gold" / "pasa-dev.gold.jsonl"  # 指定尚不存在的目标路径。
    imported = import_prepared_dataset_gold(input_path, dataset_id="pasa", split="dev", output_path=output_path)  # 执行只读输入和安全输出闭环。
    assert imported[0].query_id == "pasa:dev:source-001"  # 返回对象应与命名空间规则一致。
    loaded = load_jsonl(output_path, GoldQuery)  # 使用现有 GoldQuery JSONL 读取器验证兼容性。
    assert loaded == imported  # 写出内容必须可无损读回。
    output_path.write_text("preserve\n", encoding="utf-8")  # 模拟用户之后需要保护的已有输出。
    with pytest.raises(FileExistsError, match="输出已存在"):  # 目标存在时不得重新读取或覆盖输入。
        import_prepared_dataset_gold(tmp_path / "missing.jsonl", dataset_id="pasa", split="dev", output_path=output_path)
    assert output_path.read_text(encoding="utf-8") == "preserve\n"  # 已有文件内容必须保持不变。


def test_cli_import_is_completely_offline(tmp_path: Path) -> None:
    """CLI 应只转换明确输入文件，并返回不含在线资源的成功摘要。"""
    input_path = tmp_path / "prepared.jsonl"  # 创建 CLI 使用的本地源文件。
    input_path.write_text(json.dumps(_record().model_dump(mode="json"), ensure_ascii=False) + "\n", encoding="utf-8")  # 写入一条合成准备记录。
    output_path = tmp_path / "gold.jsonl"  # 指定新的输出文件。
    exit_code = main(["dataset-gold-import", "--input", str(input_path), "--dataset", "pasa", "--split", "dev-small", "--output", str(output_path)])  # 执行默认离线 CLI 分支。
    assert exit_code == 0  # 成功转换应返回零退出码。
    assert load_jsonl(output_path, GoldQuery)[0].metadata["split"] == "dev-small"  # 验证 CLI 显式 split 被冻结到输出元数据。
