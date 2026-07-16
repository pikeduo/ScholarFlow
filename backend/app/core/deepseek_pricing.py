"""提供不依赖服务聚合包的 DeepSeek 人民币费用估算。"""

from dataclasses import dataclass  # 用不可变对象返回费用与峰时标记。
from datetime import datetime  # 按供应商公布的北京时间判断峰谷时段。
from zoneinfo import ZoneInfo  # 使用 IANA 时区避免服务器部署时区影响计费。


_BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")  # 北京时间与上海时区使用同一 UTC 偏移规则。
_TOKENS_PER_MILLION = 1_000_000  # DeepSeek 官方价格统一以每百万 Token 表示。
_MODEL_PRICE_CNY_PER_MILLION = {  # 维护当前官方模型每百万 Token 的基础人民币价格。
    "deepseek-v4-flash": (0.02, 1.0, 2.0),  # 依次为缓存命中输入、未命中输入和输出。
    "deepseek-v4-pro": (0.025, 3.0, 6.0),  # Pro 模型使用独立的官方基础价格。
}
_FLASH_MODEL_ALIASES = frozenset({"deepseek-chat", "deepseek-reasoner"})  # 兼容仍映射至 V4-Flash 的旧模型名。


@dataclass(frozen=True)
class DeepSeekCostEstimate:
    """保存一次 DeepSeek 调用的费用估算与峰时定价标记。"""

    cost_cny: float  # 保存基于供应商实际 Token usage 计算的人民币金额。
    peak_pricing_applied: bool  # 标记本次调用是否落在工作时间的两倍费率窗口。


class DeepSeekCostCalculator:
    """集中处理模型别名、缓存 Token 和北京时间峰谷倍率。"""

    def estimate(
        self,
        model_name: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        prompt_cache_hit_tokens: int,
        prompt_cache_miss_tokens: int,
        occurred_at: datetime | None = None,
    ) -> DeepSeekCostEstimate:
        """按一次供应商响应中的 usage 计算费用估算。

        参数：
            model_name：供应商响应声明或请求配置使用的模型名。
            prompt_tokens：响应报告的全部输入 Token 数，用于兼容旧响应缺少缓存拆分字段。
            completion_tokens：响应报告的输出 Token 数。
            prompt_cache_hit_tokens：命中供应商上下文缓存的输入 Token 数。
            prompt_cache_miss_tokens：未命中供应商上下文缓存的输入 Token 数。
            occurred_at：可注入的调用时刻，未提供时使用当前北京时间。
        返回：
            DeepSeekCostEstimate：稳定的人民币估算金额与峰时标记。
        异常：
            ValueError：模型名不受当前价格表支持时抛出，禁止静默套用错误费率。
        """
        normalized_model = self._normalize_model_name(model_name)  # 统一处理供应商可能返回的旧模型别名。
        prices = _MODEL_PRICE_CNY_PER_MILLION.get(normalized_model)  # 获取命中、未命中与输出三类基础单价。
        if prices is None:  # 未知模型不能以 Flash 或 Pro 的费率猜测计费。
            raise ValueError(f"暂不支持模型 {normalized_model} 的费用估算")  # 交由调用方保留可解释的安全降级。
        safe_prompt_tokens = max(0, int(prompt_tokens))  # 防御异常或缺失 usage，保持费用非负。
        safe_completion_tokens = max(0, int(completion_tokens))  # 防御异常或缺失输出 usage，保持费用非负。
        safe_cache_hit_tokens = min(safe_prompt_tokens, max(0, int(prompt_cache_hit_tokens)))  # 缓存命中不能超过全部输入。
        reported_cache_miss_tokens = max(0, int(prompt_cache_miss_tokens))  # 读取供应商可选的未命中输入 Token。
        safe_cache_miss_tokens = min(safe_prompt_tokens - safe_cache_hit_tokens, reported_cache_miss_tokens)  # 防止供应商异常字段造成重复计费。
        if safe_cache_hit_tokens + safe_cache_miss_tokens < safe_prompt_tokens:  # 旧响应可能不提供缓存拆分字段。
            safe_cache_miss_tokens = safe_prompt_tokens - safe_cache_hit_tokens  # 未拆分输入按官方未命中价格保守估算。
        hit_price, miss_price, output_price = prices  # 解构固定顺序的三类基础单价。
        base_cost_cny = (safe_cache_hit_tokens * hit_price + safe_cache_miss_tokens * miss_price + safe_completion_tokens * output_price) / _TOKENS_PER_MILLION  # 将真实 usage 转换为人民币基础费用。
        peak_pricing_applied = self._is_peak_pricing_time(occurred_at)  # 判断本次调用是否处于工作时间高峰窗口。
        multiplier = 2.0 if peak_pricing_applied else 1.0  # 高峰时段依照用户指定的已生效政策使用两倍价格。
        return DeepSeekCostEstimate(cost_cny=round(base_cost_cny * multiplier, 8), peak_pricing_applied=peak_pricing_applied)  # 固定小数精度便于 JSON、SQLite 和前端稳定展示。

    @staticmethod
    def _normalize_model_name(model_name: str) -> str:
        """将模型名规范为当前官方价格表使用的稳定标识。"""
        normalized_model = model_name.strip().lower()  # 忽略供应商大小写或首尾空白差异。
        return "deepseek-v4-flash" if normalized_model in _FLASH_MODEL_ALIASES else normalized_model  # 旧兼容名按官方映射采用 Flash 费率。

    @staticmethod
    def _is_peak_pricing_time(occurred_at: datetime | None) -> bool:
        """判断北京时间每日 09:00–12:00、14:00–18:00 是否适用峰时费率。"""
        local_time = (occurred_at or datetime.now(_BEIJING_TIMEZONE)).astimezone(_BEIJING_TIMEZONE)  # 无论服务器时区为何均转换为北京时间。
        minutes_since_midnight = local_time.hour * 60 + local_time.minute  # 将时分转换为便于边界比较的分钟数。
        return 9 * 60 <= minutes_since_midnight < 12 * 60 or 14 * 60 <= minutes_since_midnight < 18 * 60  # 端点以 [start, end) 精确表达两段工作时间。


def estimate_deepseek_cost_or_zero(
    model_name: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_cache_hit_tokens: int,
    prompt_cache_miss_tokens: int,
) -> DeepSeekCostEstimate:
    """为未知模型提供零费用降级，避免成功调用因观测字段缺失而被丢弃。"""
    try:  # 当前支持的模型按官方价表计算费用。
        return DeepSeekCostCalculator().estimate(model_name, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, prompt_cache_hit_tokens=prompt_cache_hit_tokens, prompt_cache_miss_tokens=prompt_cache_miss_tokens)  # 只使用供应商返回的用量字段。
    except ValueError:  # 新模型尚未登记价表时不应阻断实际搜索结果。
        return DeepSeekCostEstimate(cost_cny=0.0, peak_pricing_applied=False)  # 以零费用和非峰时标记表达“暂不可估算”。
