"""本地存储路径规划：附件目录跟随会话数据库唯一化。"""
from __future__ import annotations

import os
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


def _name_key(value: str) -> str:
    """文件名比较键：Windows 上大小写不敏感。"""
    return os.path.normcase(str(value or "")).strip()


def _file_exists(parent: Path, name: str) -> bool:
    """目录中是否存在该文件（按平台大小写规则比较）。"""
    if not str(name or "").strip():
        return False
    candidate = parent / name
    try:
        if candidate.is_file():
            return True
        for entry in parent.iterdir():
            if _name_key(entry.name) == _name_key(name):
                return entry.is_file()
    except OSError:
        return False
    return False


def _write_owner(parent: Path, legacy: Path, name: str) -> None:
    """写入新位置标记（可覆盖过期标记），并把旧位置标记迁出附件目录。"""
    try:
        marker = parent / OWNER_MARKER
        try:
            current = marker.read_text(encoding="utf-8").strip()
        except OSError:
            current = ""
        if current != name:
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
    if _name_key(db_path.name) == "sessions.db":
        dedicated = parent / f"{db_path.name}-attachments"
        if dedicated.exists():
            # 默认库曾因目录被自定义库认领而改用专属目录：即使所有者
            # 标记随后失效（自定义库被删除/改名），也要继续使用专属
            # 目录，否则已登记的附件会全部失联
            return dedicated
        if legacy.exists():
            owner = _read_owner(parent, legacy)
            if (
                owner
                and _name_key(owner) != _name_key(db_path.name)
                and _file_exists(parent, owner)
            ):
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
    if owner and _name_key(owner) != _name_key(db_path.name):
        if _file_exists(parent, owner):
            return dedicated
        # 标记的数据库文件已不存在（数据库被改名/移走）：标记过期，
        # 允许当前库接管旧目录，避免旧附件全部显示“文件缺失”
    # 同目录存在默认库时，共享目录归默认库所有
    if _file_exists(parent, "sessions.db") and (
        _name_key(db_path.name) != "sessions.db"
    ):
        return dedicated
    _write_owner(parent, legacy, db_path.name)
    return legacy
