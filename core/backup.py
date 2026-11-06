"""本地自动备份。

把 ``SessionStore.export_data()`` 的完整快照写到 ``data/backups/``，
默认关闭，通过 ``MEDIX_AUTO_BACKUP_HOURS`` 开启；只保留最近
``MEDIX_BACKUP_KEEP`` 份，避免磁盘无限增长。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

DEFAULT_BACKUP_KEEP = 7
MAX_BACKUP_KEEP = 100
MAX_BACKUP_HOURS = 24 * 30
MAX_BACKUP_FILE_BYTES = 128 * 1024 * 1024
#: 与 SessionStore.import_data 共享的可恢复性上限
MAX_BACKUP_MESSAGE_CHARS = 1_000_000
MAX_BACKUP_TOTAL_CHARS = 30_000_000
MAX_BACKUP_SESSION_MESSAGES = 10_000
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
BACKUP_PREFIX = "medix-backup-"
_BACKUP_WRITE_LOCK = threading.Lock()
_BACKUP_NAME = re.compile(
    r"^medix-backup-(\d{8}-\d{6})(?:-(\d+))?\.json$"
)


def _backup_sort_key(path: Path):
    match = _BACKUP_NAME.match(path.name)
    if not match:
        return ("", 0)
    return (match.group(1), int(match.group(2) or 0))


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
        (
            path
            for path in directory.glob(f"{BACKUP_PREFIX}*.json")
            if _BACKUP_NAME.match(path.name)
        ),
        key=_backup_sort_key,
        reverse=True,
    )
    for path in backups[keep:]:
        try:
            path.unlink()
            logger.info(f"Removed old local backup: {path.name}")
        except OSError as e:
            logger.warning(f"Failed to remove old backup {path.name}: {e}")


def _validate_importable_payload(payload: Dict[str, Any]) -> None:
    """拒绝写入无法通过 import_data 恢复的备份。"""
    sessions = payload.get("sessions")
    if not isinstance(sessions, list):
        return
    total_chars = 0
    for raw in sessions:
        if not isinstance(raw, dict):
            continue
        messages = raw.get("messages")
        if not isinstance(messages, list):
            continue
        if len(messages) > MAX_BACKUP_SESSION_MESSAGES:
            raise ValueError("单个会话消息过多，无法通过 Web 恢复。")
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if len(content) > MAX_BACKUP_MESSAGE_CHARS:
                raise ValueError("单条消息过长，无法通过 Web 恢复。")
            total_chars += len(content)
            if total_chars > MAX_BACKUP_TOTAL_CHARS:
                raise ValueError("备份消息总量过大，无法通过 Web 恢复。")


def create_backup(
    payload: Dict[str, Any],
    directory: Optional[Path] = None,
    keep: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Path:
    """原子写入一份完整备份，并按保留数量清理旧文件。"""
    if not isinstance(payload, dict):
        raise ValueError("备份内容必须是对象。")
    _validate_importable_payload(payload)
    target_dir = Path(directory) if directory else get_backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 同秒并发的手动/自动备份必须串行选择文件名、写入临时文件并轮转，
    # 否则多个线程会共用同一个 .tmp 或互相覆盖最终文件
    with _BACKUP_WRITE_LOCK:
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        base = f"{BACKUP_PREFIX}{stamp}"
        path = target_dir / f"{base}.json"
        counter = 1
        while path.exists():
            path = target_dir / f"{base}-{counter}.json"
            counter += 1

        fd, temp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(target_dir)
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            text = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if len(text.encode("utf-8")) > MAX_BACKUP_FILE_BYTES:
                raise ValueError("备份数据超过单个文件上限，未写入备份。")
            temp_path.write_text(text, encoding="utf-8")
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


def list_backups(directory: Optional[Path] = None) -> list[Path]:
    """按时间倒序返回合法备份文件，忽略不匹配的临时文件。"""
    target_dir = Path(directory) if directory else get_backup_dir()
    if not target_dir.exists():
        return []
    backups = [
        path for path in target_dir.glob(f"{BACKUP_PREFIX}*.json")
        if _BACKUP_NAME.match(path.name) and path.is_file()
    ]
    return sorted(backups, key=_backup_sort_key, reverse=True)


def resolve_backup(
    name: str, directory: Optional[Path] = None
) -> Path:
    """把备份文件名解析为目录内的真实路径，拒绝路径穿越。"""
    if not isinstance(name, str) or not _BACKUP_NAME.match(name):
        raise ValueError("备份文件名不合法。")
    target_dir = (
        Path(directory) if directory else get_backup_dir()
    ).resolve()
    path = (target_dir / name).resolve()
    if path.parent != target_dir or not path.is_file():
        raise FileNotFoundError(name)
    return path


def latest_backup(directory: Optional[Path] = None) -> Optional[Path]:
    backups = list_backups(directory)
    return backups[0] if backups else None


def delete_backup(name: str, directory: Optional[Path] = None) -> bool:
    """删除指定本地备份文件。

    文件名先经过 resolve_backup 校验（拒绝路径穿越），不存在时抛
    FileNotFoundError，供 API 映射为 404。
    """
    path = resolve_backup(name, directory)
    try:
        path.unlink()
    except FileNotFoundError:
        raise
    logger.info(f"Deleted local backup: {path.name}")
    return True
