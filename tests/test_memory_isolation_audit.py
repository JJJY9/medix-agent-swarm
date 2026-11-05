"""长期记忆隔离、空元数据与 Redis 降级的回归测试。"""
import asyncio
import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_loop import AgentLoop
from core.llm_client import LLMResponse, ToolCall
from core.session_context import (
    get_memory_scope,
    session_memory_scope,
    use_memory_scope,
)
from core.skill_registry import SkillRegistry
from memory import ShortTermMemory
from memory.long_term import LongTermMemory
from memory.short_term import (
    MAX_IN_MEMORY_SESSIONS,
    MAX_MESSAGES_PER_SESSION,
    ConversationHistory,
)
from memory.session_summary import SessionSummary, SessionSummaryManager
from swarm.shared_context import SharedContext, SubTask

ROOT = Path(__file__).resolve().parent.parent


class FakeMem0:
    def __init__(self, search_result=None):
        self.search_result = search_result or []
        self.searched = None
        self.added = None

    def search(self, **kwargs):
        self.searched = kwargs
        return self.search_result

    def add(self, **kwargs):
        self.added = kwargs
        return {"id": "mem-1"}


def _memory(client):
    memory = object.__new__(LongTermMemory)
    memory.enabled = True
    memory.mem0 = client
    memory.entropy_manager = None
    return memory


def test_long_term_env_key_overrides_empty_config(monkeypatch):
    import memory.long_term as long_term_module

    if not long_term_module.MEM0_AVAILABLE:
        pytest.skip("mem0 not installed")

    monkeypatch.setattr(
        long_term_module, "MEM0_CONFIG", {"api_key": ""}
    )
    monkeypatch.setenv("MEM0_API_KEY", "env-key")
    monkeypatch.setattr(
        long_term_module,
        "MemoryClient",
        lambda api_key=None: SimpleNamespace(api_key=api_key),
    )

    memory = long_term_module.LongTermMemory()

    assert memory.enabled is True
    assert memory.mem0.api_key == "env-key"


def test_long_term_search_is_scoped_by_user_id():
    client = FakeMem0(search_result=[])
    memory = _memory(client)

    memory.search_similar_sessions(
        "头痛", limit=3, user_id="medix_profile_母亲"
    )

    assert client.searched["user_id"] == "medix_profile_母亲"


def test_session_memory_scope_hashes_custom_session_id():
    scope = session_memory_scope("妈妈的头痛")
    assert scope.startswith("medix_session_")
    assert "妈妈" not in scope
    assert scope == session_memory_scope("妈妈的头痛")
    assert scope != session_memory_scope("爸爸的头痛")


def test_long_term_add_is_scoped_by_user_id():
    client = FakeMem0()
    memory = _memory(client)

    memory.add_session_summary(
        "s1", "问题", "回答", user_id="medix_session_s1"
    )

    assert client.added["user_id"] == "medix_session_s1"


def test_long_term_metadata_hashes_raw_session_id():
    client = FakeMem0()
    memory = _memory(client)

    memory.add_session_summary(
        "妈妈的偏头痛",
        "问题",
        "回答",
        user_id="hashed-scope",
        metadata={"session_id": "raw-override", "mode": "swarm"},
    )

    metadata = client.added["metadata"]
    assert "妈妈的偏头痛" not in str(metadata)
    assert "raw-override" not in str(metadata)
    assert metadata["session_id"] == session_memory_scope("妈妈的偏头痛")
    assert metadata["mode"] == "swarm"


def test_long_term_search_handles_null_metadata_and_score():
    client = FakeMem0(search_result=[
        {"id": "1", "memory": "案例一", "metadata": None, "score": None},
        {"id": "2", "memory": "案例二", "metadata": {"timestamp": "2026-01-01"},
         "score": 0.9},
        {"id": "3", "memory": "", "metadata": {}, "score": 0.5},
    ])
    memory = _memory(client)

    results = memory.search_similar_sessions("头痛", limit=5, user_id="scope")

    assert len(results) == 2
    assert results[0]["metadata"] == {}
    assert results[0]["score"] == 0.0
    assert results[1]["score"] == 0.9


def test_redis_write_failure_falls_back_to_local_copy():
    class BrokenRedis:
        def get(self, key):
            return None

        def setex(self, *args, **kwargs):
            raise RuntimeError("redis write failed")

        def delete(self, key):
            raise RuntimeError("redis delete failed")

    memory = ShortTermMemory(storage_type="memory")
    memory.storage_type = "redis"
    memory.redis_client = BrokenRedis()
    session_id = "redis-fallback-test"
    try:
        memory.add_message(session_id, "user", "头痛")
        memory.add_message(session_id, "assistant", "建议休息")
        history = memory.get_session(session_id)

        assert history is not None
        assert [msg["content"] for msg in history.messages] == [
            "头痛", "建议休息"
        ]
        # Redis 删除失败时墓碑仍生效，避免会话复活
        assert memory.clear_session(session_id) is False
        assert memory.get_session(session_id) is None
    finally:
        memory.storage_type = "memory"
        memory.redis_client = None


def test_session_summary_tolerates_non_dict_contribution():
    context = SharedContext(session_id="s1")
    subtask = SubTask(
        id="t1", type="diagnosis", description="诊断",
        assigned_agent="diagnostic_agent",
    )
    context.add_subtask(subtask)
    context.complete_subtask("t1", "diagnostic_agent", None)

    summary = SessionSummary.from_shared_context(
        session_id="s1",
        question="问题",
        shared_context=context,
        final_answer="答复",
        start_time=datetime(2026, 1, 1, 9, 0, 0),
        end_time=datetime(2026, 1, 1, 9, 1, 0),
    )

    assert summary.agents_participated[0].agent_id == "diagnostic_agent"


def test_session_summary_search_filters_query(tmp_path):
    manager = SessionSummaryManager(str(tmp_path))
    first = tmp_path / "2026-11-01" / "first.md"
    second = tmp_path / "2026-11-02" / "second.md"
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("# 高血压随访", encoding="utf-8")
    second.write_text("# 糖尿病饮食", encoding="utf-8")

    results = manager.search_similar_sessions("高血压", limit=5)

    assert results == [first]


def _load_skill(relative_path, name):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_similar_cases_skill_requires_memory_scope(monkeypatch):
    module = _load_skill(
        ".claude/skills/search-similar-cases/script/search.py",
        "skill_similar_scope",
    )

    result = asyncio.run(module.search_similar_cases("头痛"))

    assert result["total_found"] == 0
    assert "作用域" in result["answer"]


def test_similar_cases_skill_uses_scope_and_handles_null_score(monkeypatch):
    module = _load_skill(
        ".claude/skills/search-similar-cases/script/search.py",
        "skill_similar_scope_ok",
    )
    captured = {}

    class FakeLongTerm:
        enabled = True

        def search_similar_sessions(self, query, limit=5, user_id=""):
            captured["user_id"] = user_id
            captured["limit"] = limit
            return [{
                "content": "历史案例",
                "score": None,
                "metadata": None,
            }]

    import memory.long_term as long_term_module
    monkeypatch.setattr(long_term_module, "LongTermMemory", FakeLongTerm)

    with use_memory_scope("medix_profile_母亲"):
        result = asyncio.run(module.search_similar_cases("头痛", max_results=99))

    assert captured["user_id"] == "medix_profile_母亲"
    assert captured["limit"] == 10
    assert result["total_found"] == 1
    assert "60" not in result["answer"]


class ToolThenAnswerLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(
                    id="call-1",
                    name="search_similar_cases",
                    arguments={"query": "头痛"},
                )],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="最终答复", tool_calls=[], finish_reason="stop")


async def _post_process_noop(result, final_answer):
    return result


def test_agent_loop_exposes_memory_scope_to_tools():
    seen = {}

    class ScopeAgent:
        agent_id = "scope_agent"
        config = {}

        def __init__(self):
            self.llm_client = ToolThenAnswerLLM()

        def get_system_prompt(self):
            return "系统提示"

        def format_user_input(self, data):
            return data["question"]

        def get_tools_for_llm(self):
            return []

        async def execute_tool(self, tool_name, arguments):
            seen["scope"] = get_memory_scope()
            return {"success": True}

        async def post_process_result(self, result, final_answer):
            return result

    loop = AgentLoop()
    asyncio.run(loop.run(
        ScopeAgent(),
        {
            "question": "问题",
            "context": {"memory_scope": "medix_profile_母亲"},
        },
        session_id="scope-session",
    ))

    assert seen["scope"] == "medix_profile_母亲"
    assert get_memory_scope() == ""


def test_short_term_memory_has_bounded_storage():
    history = ConversationHistory(session_id="cap")
    for i in range(MAX_MESSAGES_PER_SESSION + 5):
        history.add_message("user", f"消息 {i}")
    assert len(history.messages) == MAX_MESSAGES_PER_SESSION
    assert history.messages[-1]["content"].endswith(
        str(MAX_MESSAGES_PER_SESSION + 4)
    )

    memory = ShortTermMemory(storage_type="memory")
    created = [f"cap-{i}" for i in range(MAX_IN_MEMORY_SESSIONS + 5)]
    try:
        for session_id in created:
            memory.create_session(session_id)
        assert len(memory.sessions) <= MAX_IN_MEMORY_SESSIONS
    finally:
        for session_id in created:
            memory.clear_session(session_id)


def test_sync_skill_receives_memory_scope():
    registry = SkillRegistry()

    def read_scope():
        return {"scope": get_memory_scope()}

    registry.register("scope_sync_test", read_scope, "读取作用域", [])
    with use_memory_scope("medix_profile_测试"):
        result = asyncio.run(registry.execute("scope_sync_test"))

    assert result["scope"] == "medix_profile_测试"


def test_long_term_search_tolerates_none_limit():
    client = FakeMem0(search_result=[])
    memory = _memory(client)

    assert memory.search_similar_sessions("头痛", limit=None) == []
    assert client.searched["limit"] == 10
