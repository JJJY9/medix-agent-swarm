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
import time
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


#: mtime 比当前时间晚超过该容差：视为时钟回拨/快时钟机器的异常文件
FUTURE_MTIME_TOLERANCE_SECONDS = 300


def _session_db_path() -> Path:
    """当前会话库路径：与 SessionStore 的解析规则保持一致。"""
    raw = (os.getenv("MEDIX_SESSION_DB") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / "data" / "sessions.db"


def backup_dir_for_db(db_path: Path) -> Path:
    """为会话库返回默认备份目录，避免多个库共用一个备份目录。

    默认库沿用 ``data/backups``（兼容既有备份与恢复入口）；其它库
    使用 ``<完整文件名>-backups``（与附件目录同一规则，X.db 与
    X.sqlite 必须区分）。否则两个实例会互相推迟自动备份、按同一
    保留窗口轮转对方的快照，恢复入口还会混入另一库的数据。
    """
    db_path = Path(db_path)
    try:
        resolved = db_path.resolve()
    except OSError:
        resolved = db_path
    default_db = PROJECT_ROOT / "data" / "sessions.db"
    try:
        is_default = resolved == default_db.resolve()
    except OSError:
        is_default = resolved == default_db
    if is_default:
        return DEFAULT_BACKUP_DIR
    return resolved.parent / f"{resolved.name}-backups"


def get_backup_dir() -> Path:
    raw = (os.getenv("MEDIX_BACKUP_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return backup_dir_for_db(_session_db_path())


def ensure_backup_target_ready(
    directory: Optional[Path] = None,
) -> Path:
    """在导出整库之前确认备份目录可创建、可写。

    备份目录指向已断开的移动盘、只读目录或磁盘满时，先做全量导出
    再失败会长时间占用数据库全局锁；这里用临时文件提前探查。
    """
    target_dir = Path(directory) if directory else get_backup_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OSError(
            f"备份目录不可创建：{e.strerror or e}"
        ) from e
    try:
        fd, probe = tempfile.mkstemp(
            prefix=f"{BACKUP_PREFIX}probe.", suffix=".tmp",
            dir=str(target_dir),
        )
        os.close(fd)
        os.unlink(probe)
    except OSError as e:
        raise OSError(f"备份目录不可写：{e.strerror or e}") from e
    return target_dir


def _is_future_mtime(path: Path, now: Optional[float] = None) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    reference = time.time() if now is None else now
    return mtime - reference > FUTURE_MTIME_TOLERANCE_SECONDS


def _collect_backups(directory: Path) -> list[Path]:
    """按名称倒序收集备份；时间戳在未来的异常文件排到最后。

    时钟回拨后新备份的文件名小于回拨前的旧快照：如果仍按名称把
    未来文件排在最前，轮转会把真正的新备份挤出保留窗口，恢复
    入口也会默认选中未来时间的旧快照。
    """
    backups = [
        path for path in directory.glob(f"{BACKUP_PREFIX}*.json")
        if _BACKUP_NAME.match(path.name) and path.is_file()
    ]
    backups.sort(key=_backup_sort_key, reverse=True)
    now = time.time()
    normal: list[Path] = []
    future: list[Path] = []
    for path in backups:
        (future if _is_future_mtime(path, now) else normal).append(path)
    return normal + future


class _ByteCappedWriter:
    """把 JSON 迭代块编码为 UTF-8 写入，并限制总字节数。"""

    def __init__(self, fileobj, limit: int):
        self._fileobj = fileobj
        self._limit = limit
        self.written = 0

    def write(self, chunk: str) -> int:
        data = chunk.encode("utf-8")
        self.written += len(data)
        if self.written > self._limit:
            raise ValueError("备份数据超过单个文件上限，未写入备份。")
        self._fileobj.write(data)
        return len(chunk)


def _prune_backups(
    directory: Path, keep: int, protect: Optional[Path] = None
) -> None:
    backups = _collect_backups(directory)
    # 刚创建的备份必须保留：即使系统时钟回拨导致文件名排在旧备份之后
    candidates = [
        path for path in backups
        if protect is None or path.name != protect.name
    ]
    keep_others = max(0, keep - (1 if protect is not None else 0))
    for path in candidates[keep_others:]:
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
        # 进程被强杀时 mkstemp 的 .tmp 不会被清理；过期临时文件含完整
        # 健康数据，下次创建备份时顺带删除
        cutoff = time.time() - 3600
        for stale in target_dir.glob(f"{BACKUP_PREFIX}*.json.*.tmp"):
            try:
                if stale.is_file() and stale.stat().st_mtime < cutoff:
                    stale.unlink(missing_ok=True)
            except OSError:
                continue
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
            # 流式写入：大库不再把整份 JSON 字符串与 UTF-8 字节串同时
            # 放进内存；超过文件上限时立即中止并删除临时文件
            encoder = json.JSONEncoder(
                ensure_ascii=False, separators=(",", ":")
            )
            with open(temp_path, "wb") as fp:
                writer = _ByteCappedWriter(fp, MAX_BACKUP_FILE_BYTES)
                for chunk in encoder.iterencode(payload):
                    writer.write(chunk)
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
            protect=path,
        )

    logger.info(f"Created local backup: {path.name}")
    return path


def list_backups(directory: Optional[Path] = None) -> list[Path]:
    """按时间倒序返回合法备份文件，忽略不匹配的临时文件。"""
    target_dir = Path(directory) if directory else get_backup_dir()
    if not target_dir.exists():
        return []
    return _collect_backups(target_dir)


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
