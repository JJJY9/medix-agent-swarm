"""当前请求的记忆作用域。

长期记忆按患者档案/会话隔离，避免一个患者的历史案例泄漏到另一个患者。
AgentLoop 在执行工具前设置该值，Skill 只查询当前作用域。
"""
from contextvars import ContextVar, Token
from contextlib import contextmanager
from typing import Iterator
import hashlib

_current_memory_scope: ContextVar[str] = ContextVar(
    "medix_memory_scope", default=""
)


def session_memory_scope(session_id: str) -> str:
    """没有患者档案时的会话作用域：只发送不可逆哈希。

    会话 ID 可能由客户端自定义，原文不应进入 Mem0 等外部记忆服务。
    """
    digest = hashlib.sha256(
        str(session_id or "").encode("utf-8")
    ).hexdigest()[:24]
    return f"medix_session_{digest}"


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
