"""验证 DeepSeek 费用估算的模型价表、缓存拆分与北京时间峰时边界。"""

from datetime import datetime, timezone  # 构造不依赖本机时区的确定性调用时刻。

import pytest  # 验证未知模型拒绝使用猜测费率。

from backend.app.core.deepseek_pricing import DeepSeekCostCalculator  # 测试独立且不触发服务聚合导入的费用计算服务。


def test_flash_cost_uses_cache_usage_and_peak_multiplier() -> None:
    """V4-Flash 应按缓存命中、未命中和输出 Token 分别计费，并在峰时翻倍。"""
    calculator = DeepSeekCostCalculator()  # 构造无 I/O、无全局状态的费用计算器。
    estimate = calculator.estimate(  # 使用完整缓存拆分验证三个官方基础单价均被采用。
        "deepseek-v4-flash",
        prompt_tokens=3_000_000,
        completion_tokens=500_000,
        prompt_cache_hit_tokens=1_000_000,
        prompt_cache_miss_tokens=2_000_000,
        occurred_at=datetime(2026, 7, 16, 2, 30, tzinfo=timezone.utc),  # 北京时间 10:30，位于上午工作时间峰时段。
    )

    assert estimate.cost_cny == 6.04  # 基础价 0.02 + 2 + 1 元在峰时按两倍计为 6.04 元。
    assert estimate.peak_pricing_applied is True  # 验证返回供运行快照展示的峰时审计标记。


def test_pro_cost_uses_base_rate_outside_peak_and_treats_missing_split_as_cache_miss() -> None:
    """V4-Pro 在非峰时使用基础价，旧响应缺失缓存拆分时保守按未命中输入估算。"""
    calculator = DeepSeekCostCalculator()  # 每个测试独立构造，避免共享调用时间或模型状态。
    estimate = calculator.estimate(  # 不提供缓存拆分，验证总输入不会被漏计或误作命中。
        "deepseek-v4-pro",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=0,
        occurred_at=datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc),  # 北京时间 12:00，恰好离开上午峰时段。
    )

    assert estimate.cost_cny == 9.0  # 未命中输入 3 元加输出 6 元，12:00 不再翻倍。
    assert estimate.peak_pricing_applied is False  # 验证峰时使用半开区间，结束端点不包含在内。


def test_legacy_model_alias_uses_flash_price_and_unknown_model_is_rejected() -> None:
    """兼容旧模型别名，同时禁止未知新模型被静默套用不正确费率。"""
    calculator = DeepSeekCostCalculator()  # 复用同一无副作用服务验证模型归一化边界。
    estimate = calculator.estimate(  # 使用仍可能由供应商响应返回的旧别名。
        "deepseek-chat",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        prompt_cache_hit_tokens=1_000_000,
        prompt_cache_miss_tokens=0,
        occurred_at=datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc),  # 北京时间 08:00，使用基础费率。
    )

    assert estimate.cost_cny == 0.02  # 旧别名按 V4-Flash 缓存命中输入费率计费。
    with pytest.raises(ValueError, match="暂不支持模型"):  # 未登记模型必须由调用层安全降级，而不是虚假精确计费。
        calculator.estimate("deepseek-future", prompt_tokens=1, completion_tokens=1, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1)
