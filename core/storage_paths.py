"""本地存储路径规划：附件目录跟随会话数据库唯一化。"""
from __future__ import annotations

from pathlib import Path

#: 附件目录的所有者标记文件：自定义库认领共享目录后写入库文件名
OWNER_MARKER = ".medix-owner"


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
        return legacy

    dedicated = parent / f"{db_path.stem}-attachments"
    if dedicated.exists():
        return dedicated
    if not legacy.exists():
        return dedicated

    marker = legacy / OWNER_MARKER
    try:
        owner = marker.read_text(encoding="utf-8").strip()
    except OSError:
        owner = ""
    if owner and owner != db_path.name:
        return dedicated
    # 同目录存在默认库时，共享目录归默认库所有
    if (parent / "sessions.db").exists():
        return dedicated
    if not owner:
        try:
            marker.write_text(db_path.name, encoding="utf-8")
        except OSError:
            pass
    return legacy
