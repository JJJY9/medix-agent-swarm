"""第二十三轮审查：流式取消时的档案绑定与用量记录。"""
import asyncio
from pathlib import Path

import pytest

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield session_store
    finally:
        session_store.close()


def test_save_exchange_binds_profile_on_insert(store):
    store.save_exchange("s1", "头痛", "答案", profile="妈妈")
    assert store.get_session("s1")["profile"] == "妈妈"

    # 已绑定的档案不能被后续保存覆盖
    store.save_exchange("s1", "追问", "答案2", profile="爸爸")
    assert store.get_session("s1")["profile"] == "妈妈"


def test_save_exchange_fills_unbound_profile(store):
    store.save_exchange("s2", "头痛", "答案")
    assert (store.get_session("s2")["profile"] or "") == ""
    store.save_exchange("s2", "追问", "答案2", profile="孩子")
    assert store.get_session("s2")["profile"] == "孩子"


def test_cancelled_usage_payload_reads_attributes():
    exc = asyncio.CancelledError()
    exc.usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    exc.llm_calls = 2

    assert api_server._cancelled_usage_payload(exc) == {
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "llm_calls": 2,
    }
    assert api_server._cancelled_usage_payload(asyncio.CancelledError()) == {}


def _make_context():
    from swarm.shared_context import SharedContext, Contribution

    shared = SharedContext(session_id="s1")
    shared.data["failed_usage"] = [{
        "usage": {"prompt_tokens": 30, "total_tokens": 30},
        "llm_calls": 1,
    }]
    shared.agent_contributions["diagnostic_agent"] = [Contribution(
        agent_id="diagnostic_agent",
        subtask_id="t1",
        result={
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            },
            "llm_calls": 1,
        },
    )]
    return shared


def test_coordinator_cancelled_usage_snapshot():
    from swarm.swarm_coordinator import SwarmCoordinator

    class FakeWorker:
        def __init__(self, shared):
            self.shared_context = shared

    shared = _make_context()
    coordinator = object.__new__(SwarmCoordinator)
    # 多个 Worker 共享同一个上下文时只能计一次
    coordinator.worker_pool = [FakeWorker(shared), FakeWorker(shared)]

    usage, calls = coordinator._cancelled_usage_snapshot(
        {"prompt_tokens": 5, "total_tokens": 5}, set()
    )

    assert calls == 3
    assert usage["total_tokens"] == 65

    # 上一次会诊残留的共享上下文不能计入本次取消
    usage2, calls2 = coordinator._cancelled_usage_snapshot(
        None, {id(shared)}
    )
    assert (usage2, calls2) == ({}, 0)


def test_coordinator_save_exchange_passes_profile():
    from swarm.swarm_coordinator import SwarmCoordinator

    class FakeStore:
        def __init__(self):
            self.profiles = []

        def save_exchange(
            self, session_id, question, answer,
            metadata=None, profile="",
        ):
            self.profiles.append(profile)
            return 1

    coordinator = object.__new__(SwarmCoordinator)
    coordinator.session_store = FakeStore()

    assert coordinator._save_exchange("s", "问", "答", profile="妈妈") == 1
    assert coordinator.session_store.profiles == ["妈妈"]


def test_stream_cancel_paths_are_wired():
    source = (
        Path(__file__).resolve().parents[1] / "api_server.py"
    ).read_text(encoding="utf-8")

    # 两个会诊入口都要把档案传给协调器，落库时一并绑定
    assert source.count('"patient_profile": memory_profile') >= 2
    # 取消时从包装协程捞出用量并写流水
    assert "cancelled_usage.update(_cancelled_usage_payload(exc))" in source
    assert "_record_cancelled_usage(" in source
