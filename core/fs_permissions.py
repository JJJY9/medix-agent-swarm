"""跨平台的文件权限收紧工具。

Windows 上 ``os.chmod`` 基本不生效，但不应因此报错；POSIX 上用于把
含健康数据的目录与文件限制为仅当前用户可读写。
"""
from __future__ import annotations

import os
from pathlib import Path


def restrict_permissions(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        pass
