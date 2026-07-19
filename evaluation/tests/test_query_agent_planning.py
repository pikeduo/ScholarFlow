"""测试评测 Query Agent 只接收既有 QueryIntent 且必须显式授权。"""

import asyncio  # 在同步 pytest 中执行异步规划运行器。
import json  # 构造最小 QueryIntent manifest 与读取审计输出。
from pathlib import Path  # 使用 pytest 临时目录隔离输入和输出。

import pytest  # 覆盖授权、输入和输出保护边界。

from backend.app.models.natural_search import QueryPlanningResult  # 构造不访问 LLM 的规划返回值。
from backend.app.models.query_intent import QueryIntent  # 构造生产契约兼容的合成输入和输出。
from evaluation.cli import main  # 验证 CLI 授权边界。
from evaluation.runners.query_agent_planning import _query_intent_filename, plan_query_intents_to_files  # 测试受控规划文件入口与 Windows 文件名边界。


class _FakePlanner:
    """记录请求并返回固定 QueryIntent 的零网络 Query Agent 替身。"""

    def __init__(self) -> None:
        """初始化内存请求记录。"""
        self.requests = []  # 保存每次规划实际收到的自然语言请求。

    async def plan(self, request):
        """只依据测试请求返回结构化英文检索表达式。"""
        self.requests.append(request)  # 验证运行器不传递 Gold 或候选信息。
        return QueryPlanningResult(query_intent=QueryIntent(original_query=request.query, normalized_query="structured retrieval expression", query_language="en", research_topics=["structured retrieval expression"], source_recall_count=99, target_paper_count=request.target_paper_count, domains=["artificial intelligence"]), model_name="fake-query-agent", prompt_tokens=12, completion_tokens=8, estimated_cost_cny=0.01, duration_ms=3)  # 返回与生产契约兼容的合成结果。


def _write_source_input(tmp_path: Path) -> tuple[Path, str]:
    """写入不包含 Gold、候选或报告字段的最小 QueryIntent 输入与 manifest。"""
    query_id = "synthetic:dev:query-001"  # 使用含冒号的标识验证 Windows 安全文件名。
    source_path = tmp_path / "source.query-intent.json"  # 创建明确的本地输入文件。
    source_intent = QueryIntent(original_query="Which methods improve retrieval?", normalized_query="methods improve retrieval", query_language="en", source_recall_count=50, target_paper_count=20, enable_semantic_ranking=False, enable_cross_encoder_ranking=False, requires_web_evidence=False)  # 构造评测候选导出安全的原始意图。
    source_path.write_text(source_intent.model_dump_json() + "\n", encoding="utf-8")  # 写入可由生产契约重新读取的 JSON。
    manifest_path = tmp_path / "input.manifest.json"  # 创建当前确认的 QueryIntent manifest 版本。
    manifest_path.write_text(json.dumps({"schema_version": "query-intent-manifest-v1", "generation_strategy": "synthetic", "query_id_order": [query_id], "query_intent_files": {query_id: source_path.as_posix()}}, ensure_ascii=False), encoding="utf-8")  # 只提供映射所需的最小字段。
    return manifest_path, query_id  # 返回后续运行器所需的输入路径与选择标识。


def test_plans_only_from_source_query_intent_and_freezes_usage(tmp_path: Path) -> None:
    """规划器只能收到原问题及显式条件，输出必须恢复评测固定候选参数。"""
    input_manifest, query_id = _write_source_input(tmp_path)  # 准备无 Gold 的本地源意图。
    planner = _FakePlanner()  # 注入不访问网络的替身。
    output_dir = tmp_path / "planned"  # 选择尚不存在的 QueryIntent 输出目录。
    output_manifest = tmp_path / "planned.manifest.json"  # 选择尚不存在的审计文件。
    audit = asyncio.run(plan_query_intents_to_files(planner=planner, input_manifest_path=input_manifest, query_ids=[query_id], output_dir=output_dir, manifest_path=output_manifest))  # 执行受控规划。
    assert planner.requests[0].query == "Which methods improve retrieval?"  # 验证唯一语义输入是源 QueryIntent 原问题。
    assert planner.requests[0].target_paper_count == 20  # 验证固定最终数量从源意图继承。
    assert audit["academic_api_calls"] == 0 and audit["deepseek_calls"] == 1  # 规划阶段不得调用学术 API，但必须审计一次 LLM 调用。
    output_path = Path(audit["query_intent_files"][query_id])  # 读取 manifest 冻结的输出映射。
    planned = QueryIntent.model_validate_json(output_path.read_text(encoding="utf-8"))  # 使用生产契约验证落盘结果。
    assert planned.normalized_query == "structured retrieval expression"  # 验证保留替身生成的检索表达式。
    assert planned.source_recall_count == 50 and planned.target_paper_count == 20  # 验证不能让 Query Agent 改写候选规模。
    assert planned.retrieval_round == 1 and not planned.enable_semantic_ranking and not planned.enable_cross_encoder_ranking and not planned.requires_web_evidence  # 验证 snapshot-export 固定边界。
    assert planned.subqueries == []  # 评测第一轮不带入多轮子查询。
    with pytest.raises(FileExistsError, match="输出目录已存在"):  # 已发布输出不得覆盖。
        asyncio.run(plan_query_intents_to_files(planner=planner, input_manifest_path=input_manifest, query_ids=[query_id], output_dir=output_dir, manifest_path=tmp_path / "other.manifest.json"))  # 再次执行必须在调用前失败。


def test_cli_rejects_query_agent_without_explicit_authorization(tmp_path: Path) -> None:
    """CLI 未携带授权开关时不得读取输入或构造规划器。"""
    with pytest.raises(SystemExit) as error:  # argparse 的拒绝应以非零退出终止。
        main(["query-agent-plan", "--input-manifest", str(tmp_path / "missing.json"), "--query-id", "query-001", "--output-dir", str(tmp_path / "output"), "--manifest", str(tmp_path / "manifest.json")])  # 不提供 --allow-query-agent。
    assert error.value.code != 0  # 验证未授权不会伪装为成功。


def test_query_agent_output_filename_is_windows_safe() -> None:
    """查询标识的保留字符不得直接进入 Windows 输出文件名。"""
    filename = _query_intent_filename(1, "dataset:split:question<>\\|?*\u0001")  # 保持三段式 ID，并在最后一段构造全部非冒号保留字符。
    assert filename == "001_question_______.query-intent.json"  # 仅保留稳定序号和可写入的安全文本。
