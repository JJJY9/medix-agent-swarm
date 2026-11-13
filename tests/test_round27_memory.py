"""第二十七轮审查：长期记忆重试、短期记忆作用域与熵去重回归测试。"""
import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

import api_server
from core.session_context import profile_memory_scope, use_session_context
from memory.entropy_manager import MemoryEntropyManager
from memory.long_term import LongTermMemory
from memory.short_term import ShortTermMemory

ROOT = Path(__file__).resolve().parents[1]


def _load_search_history_module():
    path = ROOT / ".claude" / "skills" / "search-history" / "script" / "search.py"
    spec = spec_from_file_location("round27_search_history", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_long_term_memory_reinitializes_after_transient_failure(monkeypatch):
    import memory.long_term as long_term_module

    if not getattr(long_term_module, "MEM0_AVAILABLE", False):
        pytest.skip("mem0ai 未安装（CI 离线环境）")
    monkeypatch.setenv("MEM0_API_KEY", "test-key")
    calls = {"count": 0}

    class FakeClient:
        def __init__(self, api_key=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("network unreachable")

    monkeypatch.setattr("memory.long_term.MemoryClient", FakeClient)
    memory = LongTermMemory()

    assert memory.enabled is False
    assert memory.api_key_configured is True

    assert memory.ensure_available(force=True) is True
    assert memory.enabled is True
    assert calls["count"] == 2


def test_get_long_term_memory_retries_disabled_coordinator(monkeypatch):
    class FakeMemory:
        enabled = False
        api_key_configured = True

        def ensure_available(self, force=False):
            self.enabled = True
            return True

    class FakeCoordinator:
        long_term_memory = FakeMemory()

    coordinator = FakeCoordinator()
    monkeypatch.setattr(
        api_server.state, "coordinator", coordinator, raising=False
    )
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)

    memory = api_server._get_long_term_memory()

    assert memory is coordinator.long_term_memory
    assert memory.enabled is True


def test_search_history_skill_uses_bound_session(monkeypatch):
    module = _load_search_history_module()
    short_term = ShortTermMemory()
    for session in ("scope-a", "scope-b"):
        short_term.clear_session(session)
    short_term.add_message("scope-a", "user", "A 会话的问题")
    short_term.add_message("scope-a", "assistant", "A 会话的回答")
    short_term.add_message("scope-b", "user", "B 会话的问题")
    short_term.add_message("scope-b", "assistant", "B 会话的回答")

    with use_session_context("scope-a", "scope-a"):
        result = asyncio.run(
            module.search_history(session_id="scope-b", limit=10)
        )

    assert "A 会话的问题" in result["answer"]
    assert "B 会话的问题" not in result["answer"]
    for session in ("scope-a", "scope-b"):
        short_term.clear_session(session)


def test_profile_memory_scope_matches_api_namespace():
    from core.session_context import session_memory_scope

    assert api_server._memory_scope("妈妈", "s1") == profile_memory_scope("妈妈")
    assert profile_memory_scope("") == ""
    assert api_server._memory_scope("", "s1") == session_memory_scope("s1")


def test_entropy_dedupe_keeps_repeated_turns():
    engine = MemoryEntropyManager()
    messages = [
        {"role": "user", "content": "头痛怎么办"},
        {"role": "assistant", "content": "建议休息并观察"},
        {"role": "user", "content": "头痛怎么办"},
        {"role": "assistant", "content": "可以再测量血压"},
    ]

    result = engine.deduplicate_messages(messages)

    assert result == messages


def test_entropy_dedupe_removes_adjacent_retries():
    engine = MemoryEntropyManager()
    messages = [
        {"role": "user", "content": "头痛怎么办"},
        {"role": "assistant", "content": "建议休息"},
        {"role": "assistant", "content": "建议休息"},
        {"role": "system", "content": "【历史摘要】"},
        {"role": "system", "content": "【历史摘要】"},
    ]

    result = engine.deduplicate_messages(messages)

    assert result == [
        {"role": "user", "content": "头痛怎么办"},
        {"role": "assistant", "content": "建议休息"},
        {"role": "system", "content": "【历史摘要】"},
    ]
