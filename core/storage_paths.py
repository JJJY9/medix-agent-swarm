"""本地存储路径规划：附件目录跟随会话数据库唯一化。"""
from __future__ import annotations

from pathlib import Path

#: 附件目录的所有者标记文件：自定义库认领共享目录后写入库文件名
OWNER_MARKER = ".medix-attachments-owner"
#: #706 及更早版本把标记写在附件目录内部；升级时必须迁移
LEGACY_OWNER_MARKER = ".medix-owner"


def _read_owner(parent: Path, legacy: Path) -> str:
    """读取目录所有者：优先新位置，回退旧位置（附件目录内部）。"""
    for marker in (parent / OWNER_MARKER, legacy / LEGACY_OWNER_MARKER):
        try:
            owner = marker.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if owner:
            return owner
    return ""


def _write_owner(parent: Path, legacy: Path, name: str) -> None:
    """写入新位置标记，并把旧位置标记迁出附件目录。"""
    try:
        marker = parent / OWNER_MARKER
        if not marker.exists():
            marker.write_text(name, encoding="utf-8")
    except OSError:
        pass
    try:
        (legacy / LEGACY_OWNER_MARKER).unlink(missing_ok=True)
    except OSError:
        pass


def attachment_dir_for_db(db_path: Path) -> Path:
    """为指定会话库返回专属附件目录。

    默认库（sessions.db）沿用 ``parent/attachments`` 以兼容既有数据；
    同一目录下的其它数据库改用 ``<stem>-attachments``，避免完整性检查
    与孤儿清理把另一数据库仍在引用的附件当作无主文件删除。

    兼容历史用法：单个自定义库独占一个目录时仍使用 ``attachments``
    （#679 约定），但会写入所有者标记；发现目录已被其它数据库认领、
    或同目录存在默认库时，改用专属目录。
    """
    db_path = Path(db_path).resolve()
    parent = db_path.parent
    legacy = parent / "attachments"
    if db_path.name == "sessions.db":
        if legacy.exists():
            owner = _read_owner(parent, legacy)
            if owner and owner != db_path.name:
                # 目录已被自定义库认领：默认库改用专属目录，
                # 否则默认库的孤儿清理会删掉对方的附件文件。
                # 旧位置的标记也要迁移出来，避免继续留在附件目录里
                _write_owner(parent, legacy, owner)
                return parent / f"{db_path.name}-attachments"
            _write_owner(parent, legacy, db_path.name)
        return legacy

    dedicated = parent / f"{db_path.name}-attachments"
    if dedicated.exists():
        return dedicated
    if not legacy.exists():
        return dedicated

    owner = _read_owner(parent, legacy)
    if owner and owner != db_path.name:
        return dedicated
    # 同目录存在默认库时，共享目录归默认库所有
    if (parent / "sessions.db").exists():
        return dedicated
    _write_owner(parent, legacy, db_path.name)
    return legacy
