"""可选的月度 Token 预算配置。"""
from __future__ import annotations

import os

BUDGET_ENV = "MEDIX_MONTHLY_TOKEN_BUDGET"


def get_token_budget() -> int:
    """读取每月 Token 预算；未配置、非法或负数时返回 0（不启用）。"""
    raw = (os.getenv(BUDGET_ENV) or "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, value)
