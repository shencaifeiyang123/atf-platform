"""成本计算：根据 token 用量 + 模型单价表计算 LLM 调用成本。"""
from __future__ import annotations

from typing import Any

from .models import TokenUsage


def calculate_cost(
    usage_list: list[TokenUsage],
    model_prices: dict[str, dict[str, float]],
) -> float:
    """计算 token 用量列表的总成本（美元）。

    Args:
        usage_list: TokenUsage 对象列表（来自 CaseResult.token_usage 或 generation job）
        model_prices: 模型单价表，格式：
            {
              "qwen-plus": {"input_price_per_1m": 0.8, "output_price_per_1m": 2.4},
              ...
            }
            单位：美元 / 百万 token

    Returns:
        总成本（美元），保留 6 位小数
    """
    total = 0.0
    for u in usage_list:
        if not u.model:
            continue
        price_entry = model_prices.get(u.model)
        if not price_entry:
            # 模型未配置单价，跳过（不报错，避免阻塞流程）
            continue
        input_price = price_entry.get("input_price_per_1m", 0.0)
        output_price = price_entry.get("output_price_per_1m", 0.0)
        # 单价单位是 /百万 token，所以除以 1_000_000
        cost = (u.prompt_tokens * input_price + u.completion_tokens * output_price) / 1_000_000
        total += cost
    return round(total, 6)
