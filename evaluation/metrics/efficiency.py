"""汇总原始效率观测并计算透明、非官方的本地代理分。"""

import math  # 提供最近秩分位数的上取整。

from pydantic import BaseModel, Field, model_validator  # 校验代理分阈值配置。

from evaluation.contracts.prediction import PredictionRecord  # 读取预测携带的原始 usage。
from evaluation.contracts.result import EfficiencySummary  # 返回稳定效率结果。


class ProxyThreshold(BaseModel):
    """定义越低越好的线性代理分目标值与失效上限。"""

    target: float = Field(ge=0)  # 小于等于目标值时得满分。
    limit: float = Field(gt=0)  # 大于等于上限时得零分。

    @model_validator(mode="after")
    def validate_range(self) -> "ProxyThreshold":
        """确保代理分上限严格大于目标值。"""
        if self.limit <= self.target:  # 相同阈值无法形成线性区间。
            raise ValueError("代理分 limit 必须大于 target")  # 返回可理解配置错误。
        return self  # 返回通过校验的阈值。


class EfficiencyProxyConfig(BaseModel):
    """定义本地效率代理分的显式阈值和权重。"""

    academic_api_calls_per_query: ProxyThreshold = Field(default_factory=lambda: ProxyThreshold(target=3, limit=12))  # 按查询归一化来源调用，避免数据集规模影响分数。
    tokens_per_query: ProxyThreshold = Field(default_factory=lambda: ProxyThreshold(target=0, limit=40_000))  # 按查询归一化 Token，默认鼓励首轮零 LLM Token。
    latency_p95_ms: ProxyThreshold = Field(default_factory=lambda: ProxyThreshold(target=2_000, limit=120_000))  # 默认覆盖轻量离线与较慢本地排序。
    weights: dict[str, float] = Field(default_factory=lambda: {"academic_api_calls_per_query": 0.4, "tokens_per_query": 0.3, "latency_p95_ms": 0.3})  # 公开代理分组成。

    @model_validator(mode="after")
    def validate_weights(self) -> "EfficiencyProxyConfig":
        """要求三个已声明组件权重非负且总和为一。"""
        expected = {"academic_api_calls_per_query", "tokens_per_query", "latency_p95_ms"}  # 固定第一阶段代理分组件。
        if set(self.weights) != expected or any(value < 0 for value in self.weights.values()):  # 拒绝遗漏、额外或负权重。
            raise ValueError("效率代理分 weights 必须包含三个非负固定组件")  # 防止报告静默改变口径。
        if not math.isclose(sum(self.weights.values()), 1.0, abs_tol=1e-9):  # 权重必须完整分配。
            raise ValueError("效率代理分 weights 总和必须为 1")  # 阻止不透明归一化。
        return self  # 返回通过校验的配置。


def _complete_sum(values: list[int | None]) -> int | None:
    """仅在所有查询均有观测时返回完整总和。"""
    if not values or any(value is None for value in values):  # 空集合或部分缺失都不是完整观测。
        return None  # 不把未观测查询按零处理。
    return sum(value for value in values if value is not None)  # 类型收窄后计算总和。


def _observed_total_tokens(prediction: PredictionRecord) -> int | None:
    """优先使用总 Token，必要时由完整输入与输出 Token 相加。"""
    usage = prediction.usage  # 获取单次预测用量。
    if usage.total_tokens is not None:  # 供应商总量是最直接观测。
        return usage.total_tokens  # 保留原始总量。
    if usage.input_tokens is not None and usage.output_tokens is not None:  # 两个分量完整时可确定性派生。
        return usage.input_tokens + usage.output_tokens  # 避免重复要求冗余字段。
    return None  # 任一分量缺失时保持未知。


def _percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    """使用最近秩法计算小样本也可复核的分位数。"""
    if not values:  # 无耗时观测时没有分位数。
        return None  # 保持缺失。
    ordered = sorted(values)  # 排序以定位最近秩。
    rank = max(1, math.ceil(percentile * len(ordered)))  # 最近秩从一开始计数。
    return ordered[rank - 1]  # 返回对应样本值。


def _lower_is_better(value: float, threshold: ProxyThreshold) -> float:
    """将越低越好的指标线性映射到零至一。"""
    if value <= threshold.target:  # 达到目标值即满分。
        return 1.0  # 返回满分。
    if value >= threshold.limit:  # 达到失效上限即零分。
        return 0.0  # 返回零分。
    return (threshold.limit - value) / (threshold.limit - threshold.target)  # 在线性区间内平滑衰减。


def summarize_efficiency(predictions: list[PredictionRecord], config: EfficiencyProxyConfig | None = None) -> EfficiencySummary:
    """聚合效率指标；任一代理分组件缺失时不生成综合效率分。"""
    proxy_config = config or EfficiencyProxyConfig()  # 使用显式配置或第一阶段默认代理阈值。
    academic_api_calls = _complete_sum([prediction.usage.academic_api_calls for prediction in predictions])  # 汇总完整 API 观测。
    actual_http_requests = _complete_sum([prediction.usage.actual_http_requests for prediction in predictions])  # 汇总完整 HTTP 观测。
    llm_calls = _complete_sum([prediction.usage.llm_calls for prediction in predictions])  # 汇总完整 LLM 调用观测。
    total_tokens = _complete_sum([_observed_total_tokens(prediction) for prediction in predictions])  # 汇总完整 Token 观测。
    retry_count = _complete_sum([prediction.usage.retry_count for prediction in predictions])  # 汇总完整重试观测。
    rate_limit_count = _complete_sum([prediction.usage.rate_limit_count for prediction in predictions])  # 汇总完整限流观测。
    cache_hit_count = _complete_sum([prediction.usage.cache_hit_count for prediction in predictions])  # 汇总完整缓存命中观测。
    latencies = [prediction.usage.latency_ms for prediction in predictions if prediction.usage.latency_ms is not None]  # 收集已观测耗时。
    latency_complete = bool(predictions) and len(latencies) == len(predictions)  # 代理分只接受完整耗时观测。
    latency_mean = sum(latencies) / len(latencies) if latency_complete else None  # 部分缺失时不报告误导性均值。
    latency_p95 = _percentile_nearest_rank(latencies, 0.95) if latency_complete else None  # 部分缺失时不报告误导性 P95。
    query_count = len(predictions)  # 保存效率观测的预测查询数。
    raw_components: dict[str, float | None] = {"academic_api_calls_per_query": academic_api_calls / query_count if academic_api_calls is not None and query_count else None, "tokens_per_query": total_tokens / query_count if total_tokens is not None and query_count else None, "latency_p95_ms": latency_p95}  # 使用与数据集规模无关的代理组件。
    thresholds = {"academic_api_calls_per_query": proxy_config.academic_api_calls_per_query, "tokens_per_query": proxy_config.tokens_per_query, "latency_p95_ms": proxy_config.latency_p95_ms}  # 绑定显式阈值。
    proxy_components = {name: _lower_is_better(float(value), thresholds[name]) if value is not None else None for name, value in raw_components.items()}  # 逐项生成可审计分数。
    missing_fields = []  # 记录导致代理分缺失的原始观测，而非派生组件名。
    if academic_api_calls is None:  # API 总量不完整时无法计算每查询值。
        missing_fields.append("academic_api_calls")  # 报告原始缺失字段。
    if total_tokens is None:  # Token 总量不完整时无法计算每查询值。
        missing_fields.append("total_tokens")  # 报告原始缺失字段。
    if latency_p95 is None:  # 耗时不完整时无法计算 P95。
        missing_fields.append("latency_ms")  # 报告原始缺失字段。
    proxy_score = sum(proxy_components[name] * proxy_config.weights[name] for name in proxy_components) if not missing_fields else None  # 仅在全部组件存在时合成代理分。
    return EfficiencySummary(academic_api_calls=academic_api_calls, actual_http_requests=actual_http_requests, llm_calls=llm_calls, total_tokens=total_tokens, latency_mean_ms=latency_mean, latency_p95_ms=latency_p95, retry_count=retry_count, rate_limit_count=rate_limit_count, cache_hit_count=cache_hit_count, proxy_components=proxy_components, proxy_score=proxy_score, missing_fields=missing_fields)  # 返回原始值与非官方代理分。
