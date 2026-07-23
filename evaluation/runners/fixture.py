"""编排完全离线的 JSONL fixture 读取、评分与报告生成。"""

import json  # 解析 UTF-8 JSONL 和 JSON 配置。
from datetime import datetime, timezone  # 生成明确时区的报告时间。
from pathlib import Path  # 安全处理输入与输出文件路径。
from typing import TypeVar  # 标注通用 JSONL 模型加载函数。

from pydantic import BaseModel, Field  # 校验运行配置和 JSONL 记录。

from evaluation.contracts.gold import GoldQuery  # 解析金标记录。
from evaluation.contracts.prediction import PredictionRecord  # 解析预测记录。
from evaluation.contracts.result import EvaluationSummary  # 返回完整报告数据。
from evaluation.metrics.aggregate import aggregate_query_metrics  # 聚合查询级检索指标。
from evaluation.metrics.efficiency import EfficiencyProxyConfig, summarize_efficiency  # 汇总原始效率与代理分。
from evaluation.metrics.retrieval import evaluate_query  # 计算查询级检索指标。
from evaluation.metrics.structure import evaluate_structure  # 计算确定性结构代理分。
from evaluation.reports.writers import write_reports  # 输出 JSON、JSONL 和 Markdown 报告。


ModelType = TypeVar("ModelType", bound=BaseModel)  # 限制 JSONL 目标为 Pydantic 模型。


class EvaluationRunConfig(BaseModel):
    """定义与生产参数隔离的离线评分和代理分配置。"""

    evaluation_top_k: list[int] = Field(default_factory=lambda: [5, 10, 20], min_length=1)  # 只控制评分截断，不控制候选生成。
    efficiency_proxy: EfficiencyProxyConfig = Field(default_factory=EfficiencyProxyConfig)  # 保存明确非官方的效率代理阈值。
    composite_weights: dict[str, float] = Field(default_factory=lambda: {"retrieval": 0.7, "efficiency": 0.2, "structure": 0.1})  # 保存非官方综合代理权重。

    def normalized_top_k(self) -> list[int]:
        """返回去重排序后的正整数评分截断。"""
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in self.evaluation_top_k):  # 拒绝布尔值和非正整数。
            raise ValueError("evaluation_top_k 必须只包含正整数")  # 提供清晰配置错误。
        return sorted(set(self.evaluation_top_k))  # 稳定报告列顺序。

    def validate_composite_weights(self) -> None:
        """校验非官方综合代理分权重完整且总和为一。"""
        expected = {"retrieval", "efficiency", "structure"}  # 固定第一阶段综合代理组件。
        if set(self.composite_weights) != expected or any(value < 0 for value in self.composite_weights.values()):  # 禁止遗漏、额外或负权重。
            raise ValueError("composite_weights 必须包含 retrieval、efficiency、structure 三个非负权重")  # 阻止口径漂移。
        if abs(sum(self.composite_weights.values()) - 1.0) > 1e-9:  # 综合权重必须完整分配。
            raise ValueError("composite_weights 总和必须为 1")  # 拒绝隐式归一化。


def load_jsonl(path: Path, model_type: type[ModelType]) -> list[ModelType]:
    """以 UTF-8 逐行加载 JSONL，并在错误中保留文件和行号。"""
    records: list[ModelType] = []  # 按输入顺序保存记录。
    with path.open("r", encoding="utf-8") as stream:  # 显式使用仓库统一 UTF-8 编码。
        for line_number, raw_line in enumerate(stream, start=1):  # 使用一基行号便于人工定位。
            line = raw_line.strip()  # 忽略空白行和行尾换行。
            if not line:  # 空白行不构成记录。
                continue  # 读取下一行。
            try:  # 将 JSON 与字段错误包裹为带位置的异常。
                records.append(model_type.model_validate_json(line))  # 同时解析并校验契约。
            except Exception as exc:  # Pydantic 和 JSON 异常都需要明确输入位置。
                raise ValueError(f"{path} 第 {line_number} 行无效: {exc}") from exc  # 不吞掉原始错误链。
    return records  # 返回稳定输入顺序。


def load_run_config(path: Path | None) -> EvaluationRunConfig:
    """加载可选 JSON 配置，未提供时使用第一阶段默认配置。"""
    if path is None:  # 调用方未提供配置文件。
        return EvaluationRunConfig()  # 返回明确默认值。
    with path.open("r", encoding="utf-8") as stream:  # 显式使用 UTF-8 读取中文说明兼容配置。
        payload = json.load(stream)  # 解析单个 JSON 对象。
    return EvaluationRunConfig.model_validate(payload)  # 校验阈值、字段和类型。


def _index_unique(records: list, field_name: str, record_label: str) -> dict[str, object]:
    """按稳定字段建立索引并拒绝重复记录。"""
    index: dict[str, object] = {}  # 保存唯一记录索引。
    for record in records:  # 按输入顺序检查重复。
        key = getattr(record, field_name)  # 读取声明的稳定键。
        if key in index:  # 重复查询会导致指标口径不确定。
            raise ValueError(f"{record_label} 存在重复 {field_name}: {key}")  # 强制调用方修复数据。
        index[key] = record  # 保存唯一记录。
    return index  # 返回完成校验的索引。


def evaluate_records(gold_queries: list[GoldQuery], predictions: list[PredictionRecord], config: EvaluationRunConfig | None = None) -> EvaluationSummary:
    """在内存中完成一次不访问网络、API、LLM 或模型的 fixture 评测。"""
    run_config = config or EvaluationRunConfig()  # 使用显式配置或稳定默认值。
    cutoffs = run_config.normalized_top_k()  # 获取只影响评分的 Top-K。
    run_config.validate_composite_weights()  # 在计算前固定非官方综合分口径。
    gold_index = _index_unique(gold_queries, "query_id", "金标")  # 拒绝重复金标查询。
    prediction_index = _index_unique(predictions, "query_id", "预测")  # 拒绝重复预测查询。
    extra_query_ids = sorted(set(prediction_index) - set(gold_index))  # 找出没有金标的预测。
    if extra_query_ids:  # 未定义分母的预测不能静默进入部分汇总。
        raise ValueError(f"预测包含未出现在金标中的 query_id: {', '.join(extra_query_ids)}")  # 要求输入集合对齐。
    query_metrics = [evaluate_query(gold_query, prediction_index.get(gold_query.query_id), cutoffs) for gold_query in gold_queries]  # 按金标顺序评测并保留缺失预测。
    retrieval = aggregate_query_metrics(query_metrics, cutoffs)  # 计算宏微检索与排序指标。
    efficiency = summarize_efficiency(predictions, run_config.efficiency_proxy)  # 只汇总真实提供的预测 usage。
    aligned_predictions = [prediction_index.get(gold_query.query_id) or PredictionRecord(query_id=gold_query.query_id) for gold_query in gold_queries]  # 为缺失预测构造显式空结构记录。
    structure = evaluate_structure(aligned_predictions)  # 对所有金标查询计算结构代理分。
    max_k = max(cutoffs)  # 选择最大评分截断作为综合代理的检索组件。
    composite_score = None  # 效率观测不完整时不得合成看似完整的总分。
    if efficiency.proxy_score is not None:  # 三个效率组件均完整才可计算本地综合分。
        composite_score = retrieval.cutoffs[max_k].macro_f1 * run_config.composite_weights["retrieval"] + efficiency.proxy_score * run_config.composite_weights["efficiency"] + structure.mean_proxy_score * run_config.composite_weights["structure"]  # 使用公开固定权重合成。
    warnings = ["效率分、结构分和综合分均为本地代理分，不是赛题官方分数。"]  # 始终明确代理性质。
    if efficiency.missing_fields:  # 缺失观测会阻止效率和综合分生成。
        warnings.append(f"效率观测不完整，未生成本地综合代理分：{', '.join(efficiency.missing_fields)}")  # 报告具体缺失字段。
    return EvaluationSummary(generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), retrieval=retrieval, efficiency=efficiency, structure=structure, local_composite_proxy_score=composite_score, query_metrics=query_metrics, warnings=warnings)  # 返回完整离线报告契约。


def run_fixture(gold_path: Path, prediction_path: Path, output_dir: Path, config_path: Path | None = None) -> EvaluationSummary:
    """从本地 JSONL 读取 fixture，完成评分并写出三种本地报告。"""
    gold_queries = load_jsonl(gold_path, GoldQuery)  # 读取并校验金标。
    predictions = load_jsonl(prediction_path, PredictionRecord)  # 读取并校验预测。
    config = load_run_config(config_path)  # 读取独立评测配置。
    summary = evaluate_records(gold_queries, predictions, config)  # 完全离线计算所有指标。
    write_reports(summary, output_dir)  # 写出 JSON、JSONL 和 Markdown 报告。
    return summary  # 返回报告供 CLI 或测试使用。
