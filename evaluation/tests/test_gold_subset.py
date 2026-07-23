"""测试 GoldQuery 开发集子集选择与 manifest 封存的完全离线边界。"""

import hashlib  # 核对 manifest 冻结的输入和输出 SHA-256。
import json  # 读取测试生成的 manifest JSON。
from pathlib import Path  # 使用 pytest 临时目录隔离测试输入与输出。

import pytest  # 覆盖数量、重复键和已有输出等错误边界。

from evaluation.cli import main  # 验证零网络 CLI 闭环。
from evaluation.contracts.gold import GoldQuery  # 构造合成开发集金标。
from evaluation.contracts.subset import GoldSubsetManifest  # 验证 manifest 数据契约可无损读回。
from evaluation.runners.dataset_import import serialize_gold_queries  # 使用正式 GoldQuery 编码构造测试输入。
from evaluation.runners.fixture import load_jsonl  # 使用正式加载器验证输出 JSONL 兼容性。
from evaluation.runners.gold_subset import select_gold_subset, select_gold_subset_to_files  # 测试稳定选择与双文件封存入口。


def _gold_query(query_id: str) -> GoldQuery:
    """构造一条不包含真实用户查询或论文内容的合成 GoldQuery。"""
    return GoldQuery(query_id=query_id, query=f"synthetic query {query_id}", relevant_papers=[{"arxiv_id": f"2401.{len(query_id):05d}", "title": f"Synthetic Paper {query_id}"}], metadata={"dataset": "synthetic"})  # 提供可被正式 JSONL 写入器序列化的最小金标。


def _write_gold_queries(path: Path, gold_queries: list[GoldQuery]) -> None:
    """将合成 GoldQuery 写入测试专用 UTF-8 JSONL 文件。"""
    path.write_text(serialize_gold_queries(gold_queries), encoding="utf-8", newline="\n")  # 固定编码和换行，使输入哈希可在断言中复算。


def test_subset_selection_is_stable_across_input_order() -> None:
    """稳定哈希选择不得依赖源文件的 GoldQuery 行顺序。"""
    gold_queries = [_gold_query("query-c"), _gold_query("query-a"), _gold_query("query-b")]  # 构造包含三个稳定查询标识的合成输入。
    selected = select_gold_subset(gold_queries, count=2, selection_id="synthetic-dev-v1", selection_seed="seed-001")  # 按固定策略从原始顺序选择子集。
    reordered_selected = select_gold_subset(list(reversed(gold_queries)), count=2, selection_id="synthetic-dev-v1", selection_seed="seed-001")  # 用逆序输入验证选择不依赖行顺序。
    assert [query.query_id for query in selected] == [query.query_id for query in reordered_selected]  # 成员和排名都必须完全一致。
    assert len(selected) == 2  # 返回数量必须严格服从显式 count 参数。


def test_subset_selection_rejects_invalid_count_and_duplicate_query_ids() -> None:
    """子集选择不得接受越界数量或模糊的重复查询标识。"""
    gold_queries = [_gold_query("query-a"), _gold_query("query-b")]  # 构造最小非空输入集合。
    with pytest.raises(ValueError, match="count 必须为正整数"):  # 零条查询不能形成开发集评测分母。
        select_gold_subset(gold_queries, count=0, selection_id="synthetic-dev-v1", selection_seed="seed-001")
    with pytest.raises(ValueError, match="count 不能大于输入查询数"):  # 不得把不足数量的数据集伪装成完整子集。
        select_gold_subset(gold_queries, count=3, selection_id="synthetic-dev-v1", selection_seed="seed-001")
    with pytest.raises(ValueError, match="重复 query_id"):  # 重复关联键会导致候选快照与评分分母歧义。
        select_gold_subset([_gold_query("query-a"), _gold_query("query-a")], count=1, selection_id="synthetic-dev-v1", selection_seed="seed-001")


def test_subset_file_selection_writes_hashes_manifest_and_goldquery(tmp_path: Path) -> None:
    """文件入口应写出与 manifest 哈希和稳定 ID 列表一致的独立 GoldQuery 子集。"""
    input_path = tmp_path / "full.gold.jsonl"  # 构造用户已验证的完整金标输入文件。
    source_queries = [_gold_query("query-a"), _gold_query("query-b"), _gold_query("query-c")]  # 提供足够选择两条开发集查询的合成输入。
    _write_gold_queries(input_path, source_queries)  # 写入正式格式的完全离线输入。
    output_path = tmp_path / "pasa-dev-2.gold.jsonl"  # 指定尚不存在的开发集子集输出。
    manifest_path = tmp_path / "pasa-dev-2.manifest.json"  # 指定尚不存在的独立审计清单。
    manifest = select_gold_subset_to_files(input_path, count=2, selection_id="pasa-auto-dev-ranking-v1", selection_seed="20260719", output_path=output_path, manifest_path=manifest_path)  # 执行零网络子集封存。
    loaded_queries = load_jsonl(output_path, GoldQuery)  # 验证输出可被现有评分器直接加载。
    loaded_manifest = GoldSubsetManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))  # 验证 manifest 可通过正式严格契约读取。
    assert loaded_manifest == manifest  # 返回对象、JSON 文件和契约必须保持一致。
    assert [query.query_id for query in loaded_queries] == manifest.selected_query_ids  # 输出 GoldQuery 顺序必须与 manifest 的稳定排名一致。
    assert manifest.source_gold_sha256 == hashlib.sha256(input_path.read_bytes()).hexdigest()  # 输入哈希必须对应用户指定源文件的原始字节。
    assert manifest.selected_gold_sha256 == hashlib.sha256(output_path.read_bytes()).hexdigest()  # 输出哈希必须对应实际发布的规范化 JSONL 字节。
    assert manifest.source_query_count == 3  # manifest 必须记录完整输入规模。
    assert manifest.selected_query_count == 2  # manifest 必须记录显式开发集子集规模。


def test_subset_file_selection_protects_existing_manifest_before_reading_input(tmp_path: Path) -> None:
    """已有 manifest 时应在读取缺失输入前失败，避免覆盖已封存实验信息。"""
    manifest_path = tmp_path / "existing.manifest.json"  # 构造需要保护的已有审计文件。
    manifest_path.write_text("preserve\n", encoding="utf-8", newline="\n")  # 写入可验证未被改动的占位内容。
    with pytest.raises(FileExistsError, match="manifest 已存在"):  # 输出边界应优先于输入读取检查。
        select_gold_subset_to_files(tmp_path / "missing.gold.jsonl", count=1, selection_id="synthetic-dev-v1", selection_seed="seed-001", output_path=tmp_path / "new.gold.jsonl", manifest_path=manifest_path)
    assert manifest_path.read_text(encoding="utf-8") == "preserve\n"  # 已封存 manifest 内容不得被改写。


def test_subset_cli_is_completely_offline(tmp_path: Path) -> None:
    """CLI 应只处理本地 GoldQuery 并输出不含查询正文的成功摘要。"""
    input_path = tmp_path / "full.gold.jsonl"  # 创建 CLI 使用的合成完整金标文件。
    _write_gold_queries(input_path, [_gold_query("query-a"), _gold_query("query-b")])  # 写入两条可供选择的合成查询。
    output_path = tmp_path / "subset.gold.jsonl"  # 指定新的子集 GoldQuery 输出。
    manifest_path = tmp_path / "subset.manifest.json"  # 指定新的子集 manifest 输出。
    exit_code = main(["gold-subset-select", "--input", str(input_path), "--count", "1", "--selection-id", "synthetic-dev-v1", "--seed", "seed-001", "--output", str(output_path), "--manifest", str(manifest_path)])  # 通过正式 CLI 执行零网络封存分支。
    assert exit_code == 0  # 成功封存应返回零退出码。
    assert len(load_jsonl(output_path, GoldQuery)) == 1  # 输出必须严格包含用户指定的一条开发集查询。
    assert GoldSubsetManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8"))).selected_query_count == 1  # manifest 必须可独立表明本次选择规模。
