"""测试已确认 PaSa AutoScholarQuery JSONL 格式的完全离线金标转换。"""

import json  # 构造 pytest 临时原始 JSONL 和验证输出内容。
from pathlib import Path  # 使用临时目录隔离真实 PaSa 数据目录。

import pytest  # 验证字段配对、重复和文件保护边界。
from pydantic import ValidationError  # 断言原始记录契约拒绝错误字段。

from evaluation.adapters.pasa import PASA_AUTOSCHOLARQUERY_SCHEMA_VERSION, convert_pasa_records  # 验证纯内存 PaSa 映射规则。
from evaluation.cli import main  # 验证完全离线 CLI 闭环。
from evaluation.contracts.gold import GoldQuery  # 读取转换后的统一金标。
from evaluation.contracts.pasa import PasaRawQuery  # 构造已确认字段版本的合成 PaSa 记录。
from evaluation.runners.fixture import load_jsonl  # 复用正式 GoldQuery JSONL 加载器验证输出兼容性。
from evaluation.runners.pasa_import import import_pasa_gold  # 执行本地 PaSa 文件导入入口。


def _record(**updates: object) -> PasaRawQuery:
    """构造一条与用户已下载 AutoScholarQuery/dev 样例同字段的合成记录。"""
    payload: dict[str, object] = {"qid": "AutoScholarQuery_dev_0", "question": "Which papers study graph retrieval?", "answer": ["Graph Retrieval Paper", "Second Graph Paper"], "answer_arxiv_id": ["2401.00001", "2401.00002v2"], "source_meta": {"published_time": "20260719"}}  # 使用无版权和无真实查询内容的合成字段值。
    payload.update(updates)  # 允许单个测试覆盖特定字段边界。
    return PasaRawQuery.model_validate(payload)  # 使用正式 Pydantic 契约验证构造数据。


def test_pasa_conversion_preserves_titles_arxiv_ids_and_source_metadata() -> None:
    """PaSa 标题与 arXiv ID 应逐项映射，并保留可审计来源元数据。"""
    gold_queries = convert_pasa_records([_record()], split="auto-dev")  # 执行零网络纯内存转换。
    assert gold_queries[0].query_id == "pasa:auto-dev:AutoScholarQuery_dev_0"  # 输出键应包含固定数据集、切分和原始 qid。
    assert [paper.title for paper in gold_queries[0].relevant_papers] == ["Graph Retrieval Paper", "Second Graph Paper"]  # 金标标题保留 PaSa 给出的原始顺序。
    assert [paper.arxiv_id for paper in gold_queries[0].relevant_papers] == ["2401.00001", "2401.00002v2"]  # arXiv 标识必须与同索引标题配对。
    assert gold_queries[0].metadata["pasa_source_published_time"] == "20260719"  # 来源标量需写入命名空间化元数据。
    assert gold_queries[0].metadata["pasa_schema_version"] == PASA_AUTOSCHOLARQUERY_SCHEMA_VERSION  # 映射规则版本必须被冻结。


def test_pasa_contract_rejects_misaligned_ids_unknown_fields_and_duplicates() -> None:
    """字段错位、未知字段和重复论文不得静默进入评测金标。"""
    with pytest.raises(ValidationError, match="answer_arxiv_id 非空时必须与 answer 长度一致"):  # 有 arXiv 列表时必须可逐项配对。
        _record(answer_arxiv_id=["2401.00001"])
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):  # 未确认字段版本不得被适配器猜测或忽略。
        _record(unexpected_field="unsupported")
    duplicate_record = _record(answer=["Same Paper", "Same Paper"], answer_arxiv_id=["2401.11111", "2401.11111v2"])  # 构造统一 arXiv 规则可识别的重复金标论文。
    with pytest.raises(ValueError, match="重复相关论文"):  # 通用导入器必须拒绝重复而非静默合并。
        convert_pasa_records([duplicate_record], split="auto-dev")


def test_pasa_file_import_writes_goldquery_and_protects_existing_output(tmp_path: Path) -> None:
    """本地 PaSa JSONL 应写成可被评分器加载的 GoldQuery 文件，且不覆盖已有目标。"""
    input_path = tmp_path / "AutoScholarQuery" / "dev.jsonl"  # 模拟用户已下载的默认 PaSa 文件位置。
    input_path.parent.mkdir(parents=True, exist_ok=True)  # 创建仅供测试使用的本地父目录。
    input_path.write_text(json.dumps(_record().model_dump(mode="json"), ensure_ascii=False) + "\n", encoding="utf-8")  # 写入一条合成 PaSa JSONL 记录。
    output_path = tmp_path / "gold" / "pasa-auto-dev.gold.jsonl"  # 指定尚不存在的统一金标输出。
    imported = import_pasa_gold(input_path, split="auto-dev", output_path=output_path)  # 执行不访问网络的文件转换。
    assert load_jsonl(output_path, GoldQuery) == imported  # 输出必须可被现有评分器无损读取。
    output_path.write_text("preserve\n", encoding="utf-8")  # 模拟用户已审阅且需要保护的已有文件。
    with pytest.raises(FileExistsError, match="PaSa 金标输出已存在"):  # 已有目标必须在读取输入前被拒绝。
        import_pasa_gold(tmp_path / "missing.jsonl", split="auto-dev", output_path=output_path)
    assert output_path.read_text(encoding="utf-8") == "preserve\n"  # 已有内容不得被改写。


def test_pasa_cli_runs_without_network_or_model(tmp_path: Path) -> None:
    """CLI 应只转换用户指定的本地文件，并明确返回成功退出码。"""
    input_path = tmp_path / "dev.jsonl"  # 构造 CLI 使用的合成 PaSa 输入文件。
    input_path.write_text(json.dumps(_record().model_dump(mode="json"), ensure_ascii=False) + "\n", encoding="utf-8")  # 写入已确认字段版本的合成记录。
    output_path = tmp_path / "pasa.gold.jsonl"  # 指定新的输出文件。
    exit_code = main(["pasa-gold-import", "--input", str(input_path), "--split", "auto-dev", "--output", str(output_path)])  # 执行默认离线 CLI 分支。
    assert exit_code == 0  # 成功转换应返回零退出码。
    assert load_jsonl(output_path, GoldQuery)[0].metadata["dataset"] == "pasa"  # 输出应固定标识为 PaSa 数据集。
