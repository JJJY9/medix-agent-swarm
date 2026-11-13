"""项目 config.py 的确定性加载。

历史实现按固定目录深度在「项目上一层」导入 config.py，这在
``.claude/worktrees/<name>`` 布局下找不到文件（静默退回默认值），
克隆到含无关 config.py 的目录时又会执行别的项目的文件。这里改为
显式路径加载：

1. ``MEDIX_CONFIG`` 显式指定的文件；
2. 仓库根目录的 ``config.py``；
3. 兼容旧习惯：项目上一层的 ``config.py``。

按文件路径执行模块，不注册到 ``sys.modules``，也不把目录插到
``sys.path`` 头部，避免同名模块遮蔽；同一进程内按路径缓存。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 已加载的 config.py：路径 -> 模块对象（None 表示加载失败）
_MODULE_CACHE: Dict[Path, Any] = {}


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    explicit = (os.getenv("MEDIX_CONFIG") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = (_REPO_ROOT / path).resolve()
        candidates.append(path)
    candidates.append(_REPO_ROOT / "config.py")
    candidates.append(_REPO_ROOT.parent / "config.py")
    return candidates


def _load_config_module(path: Path) -> Optional[Any]:
    resolved = path
    try:
        resolved = path.resolve()
    except OSError:
        pass
    if resolved in _MODULE_CACHE:
        return _MODULE_CACHE[resolved]
    module = None
    if resolved.is_file():
        added_path: Optional[str] = None
        config_dir = str(resolved.parent)
        # config.py 可能导入同目录的辅助模块；追加到 sys.path 末尾
        # （不是开头），执行完立即移除，避免长期遮蔽同名模块
        if config_dir not in sys.path:
            sys.path.append(config_dir)
            added_path = config_dir
        try:
            spec = importlib.util.spec_from_file_location(
                "medix_user_config", resolved
            )
            if spec is not None and spec.loader is not None:
                loaded = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(loaded)
                module = loaded
        except Exception as exc:  # noqa: BLE001 - 用户配置出错不应崩溃
            logger.warning(
                "读取 config.py 失败（已忽略）："
                f"{type(exc).__name__}"
            )
            module = None
        finally:
            if added_path is not None:
                try:
                    sys.path.remove(added_path)
                except ValueError:
                    pass
    _MODULE_CACHE[resolved] = module
    return module


def load_config_module() -> Optional[Any]:
    """返回第一个可用的 config.py 模块；都不存在时返回 None。"""
    for path in _candidate_paths():
        try:
            exists = path.is_file()
        except OSError:
            exists = False
        if not exists:
            continue
        module = _load_config_module(path)
        if module is not None:
            return module
    return None


def load_config_value(name: str) -> Any:
    """读取 config.py 中的变量；文件缺失或变量不存在时返回 None。"""
    module = load_config_module()
    if module is None:
        return None
    return getattr(module, name, None)


def describe_config_source() -> str:
    """返回实际使用的 config.py 路径（供诊断展示），没有时返回空串。"""
    for path in _candidate_paths():
        try:
            if path.is_file() and _load_config_module(path) is not None:
                return str(path)
        except OSError:
            continue
    return ""
