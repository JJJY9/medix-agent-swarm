"""当前请求的记忆作用域。

长期记忆按患者档案/会话隔离，避免一个患者的历史案例泄漏到另一个患者。
AgentLoop 在执行工具前设置该值，Skill 只查询当前作用域。
"""
from contextvars import ContextVar, Token
from contextlib import contextmanager
from typing import Iterator

_current_memory_scope: ContextVar[str] = ContextVar(
    "medix_memory_scope", default=""
)


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
