"""当前请求的记忆作用域。

长期记忆按患者档案/会话隔离，避免一个患者的历史案例泄漏到另一个患者。
AgentLoop 在执行工具前设置该值，Skill 只查询当前作用域。
"""
from contextvars import ContextVar, Token
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import hashlib
import hmac
import os
import secrets
import threading

_current_memory_scope: ContextVar[str] = ContextVar(
    "medix_memory_scope", default=""
)

_current_session_id: ContextVar[str] = ContextVar(
    "medix_session_id", default=""
)


_MEMORY_SCOPE_SALT_LOCK = threading.Lock()
_memory_scope_salt_cache = ""


def _memory_scope_salt() -> str:
    """每安装独立随机盐（持久化在会话库同目录）。

    患者档案名空间很小（妈妈/爸爸/常见中文姓名），无盐 SHA-256
    可离线穷举还原姓名并与病史摘要关联；加盐后同一姓名在不同
    安装的命名空间互不相同，导出/云端数据也无法跨安装关联。
    可由 MEDIX_MEMORY_SCOPE_SALT 显式覆盖；无法落盘时退回进程内
    随机盐（隐私优先，重启后旧命名空间不再命中）。
    """
    global _memory_scope_salt_cache
    with _MEMORY_SCOPE_SALT_LOCK:
        if _memory_scope_salt_cache:
            return _memory_scope_salt_cache
        salt = (os.environ.get("MEDIX_MEMORY_SCOPE_SALT") or "").strip()
        if not salt:
            db_env = (os.environ.get("MEDIX_SESSION_DB") or "").strip()
            base = Path(db_env).expanduser().parent if db_env else Path("data")
            path = base / ".memory_scope_salt"
            try:
                if path.exists():
                    salt = path.read_text(encoding="utf-8").strip()
                if not salt:
                    salt = secrets.token_hex(32)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(salt, encoding="utf-8")
            except OSError:
                salt = secrets.token_hex(32)
        _memory_scope_salt_cache = salt[:128]
        return _memory_scope_salt_cache


def _scope_digest(value: str) -> str:
    return hmac.new(
        _memory_scope_salt().encode("utf-8"),
        str(value or "").encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]


def session_memory_scope(session_id: str) -> str:
    """没有患者档案时的会话作用域：带盐哈希。

    会话 ID 可能由客户端自定义，原文不应进入 Mem0 等外部记忆服务。
    """
    return f"medix_session_{_scope_digest(session_id)}"


def profile_memory_scope(profile: str) -> str:
    """患者档案的长期记忆命名空间（带盐哈希），空档案返回空串。"""
    name = " ".join(str(profile or "").split())
    if not name:
        return ""
    return f"medix_profile_{_scope_digest(name)}"


def get_memory_scope() -> str:
    return _current_memory_scope.get()


def set_memory_scope(scope: str) -> Token:
    return _current_memory_scope.set(str(scope or "")[:120])


def reset_memory_scope(token: Token) -> None:
    _current_memory_scope.reset(token)


@contextmanager
def use_memory_scope(scope: str) -> Iterator[None]:
    token = set_memory_scope(scope)
    try:
        yield
    finally:
        reset_memory_scope(token)


def get_current_session_id() -> str:
    return _current_session_id.get()


@contextmanager
def use_session_context(session_id: str, scope: str = "") -> Iterator[None]:
    """同时绑定当前会话 ID 与记忆作用域。

    短期记忆类 Skill（如 search-history）只能读取当前会话，模型传入的
    session_id 不能越过这个边界。
    """
    session_token = _current_session_id.set(
        str(session_id or "").strip()[:120]
    )
    scope_token = set_memory_scope(scope)
    try:
        yield
    finally:
        reset_memory_scope(scope_token)
        _current_session_id.reset(session_token)
