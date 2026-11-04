"""数据保留策略。

自动归档不会删除任何会话；超过保留期的记录只是从默认历史列表移到
「已归档」视图，用户仍可搜索、导出或恢复。
"""
from __future__ import annotations

import os

#: 允许的最大自动归档天数（约 10 年）
MAX_AUTO_ARCHIVE_DAYS = 3650


def get_auto_archive_days() -> int:
    """读取 MEDIX_AUTO_ARCHIVE_DAYS；0 或无效值表示关闭。"""
    raw = (os.getenv("MEDIX_AUTO_ARCHIVE_DAYS") or "").strip()
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return 0
    if days <= 0:
        return 0
    return min(days, MAX_AUTO_ARCHIVE_DAYS)
