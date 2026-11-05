"""本地自动备份。

把 ``SessionStore.export_data()`` 的完整快照写到 ``data/backups/``，
默认关闭，通过 ``MEDIX_AUTO_BACKUP_HOURS`` 开启；只保留最近
``MEDIX_BACKUP_KEEP`` 份，避免磁盘无限增长。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

DEFAULT_BACKUP_KEEP = 7
MAX_BACKUP_KEEP = 100
MAX_BACKUP_HOURS = 24 * 30
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
BACKUP_PREFIX = "medix-backup-"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def get_auto_backup_hours() -> int:
    """自动备份间隔（小时）；0 表示关闭。"""
    return _env_int("MEDIX_AUTO_BACKUP_HOURS", 0, 0, MAX_BACKUP_HOURS)


def get_backup_keep() -> int:
    """保留的备份份数。"""
    return _env_int(
        "MEDIX_BACKUP_KEEP", DEFAULT_BACKUP_KEEP, 1, MAX_BACKUP_KEEP
    )


def get_backup_dir() -> Path:
    raw = (os.getenv("MEDIX_BACKUP_DIR") or "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_BACKUP_DIR


def _prune_backups(directory: Path, keep: int) -> None:
    backups = sorted(
        directory.glob(f"{BACKUP_PREFIX}*.json"),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in backups[keep:]:
        try:
            path.unlink()
            logger.info(f"Removed old local backup: {path.name}")
        except OSError as e:
            logger.warning(f"Failed to remove old backup {path.name}: {e}")


def create_backup(
    payload: Dict[str, Any],
    directory: Optional[Path] = None,
    keep: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Path:
    """原子写入一份完整备份，并按保留数量清理旧文件。"""
    if not isinstance(payload, dict):
        raise ValueError("备份内容必须是对象。")
    target_dir = Path(directory) if directory else get_backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = f"{BACKUP_PREFIX}{stamp}"
    path = target_dir / f"{base}.json"
    counter = 1
    while path.exists():
        path = target_dir / f"{base}-{counter}.json"
        counter += 1

    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
        try:
            # 备份含健康数据，尽量只允许当前用户读取（Windows 上忽略权限位）
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    _prune_backups(
        target_dir,
        max(1, keep if keep is not None else get_backup_keep()),
    )
    logger.info(f"Created local backup: {path.name}")
    return path


def latest_backup(directory: Optional[Path] = None) -> Optional[Path]:
    target_dir = Path(directory) if directory else get_backup_dir()
    if not target_dir.exists():
        return None
    backups = sorted(
        target_dir.glob(f"{BACKUP_PREFIX}*.json"),
        key=lambda path: path.name,
    )
    return backups[-1] if backups else None
