"""可选的模型费用估算配置。

费用只用于展示，实际账单以模型服务商为准。单位：每百万 Token。
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, Optional

INPUT_PRICE_ENV = "MEDIX_PRICE_INPUT"
OUTPUT_PRICE_ENV = "MEDIX_PRICE_OUTPUT"
CURRENCY_ENV = "MEDIX_CURRENCY"
DEFAULT_CURRENCY = "¥"


def _read_price(name: str) -> Optional[float]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    return max(0.0, value)


def get_pricing() -> Optional[Dict[str, Any]]:
    """返回前端估算费用所需的单价；未配置或全为 0 时返回 None。"""
    input_price = _read_price(INPUT_PRICE_ENV)
    output_price = _read_price(OUTPUT_PRICE_ENV)
    if not input_price and not output_price:
        return None

    currency = (os.getenv(CURRENCY_ENV) or DEFAULT_CURRENCY).strip()
    return {
        "input_per_million": input_price or 0.0,
        "output_per_million": output_price or 0.0,
        "currency": currency or DEFAULT_CURRENCY,
    }
