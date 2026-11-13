"""第二十二轮审查：失败调用用量与流式事件回归测试。"""
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.llm_client import LLMError
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


class FailedCoordinator:
    worker_pool = []

    async def process(self, question, context=None, session_id=None, **kwargs):
        error = LLMError("llm_server", "服务暂时不可用", retryable=True)
        error.usage = {
            "prompt_tokens": 100,
            "completion_tokens": 23,
            "total_tokens": 123,
        }
        error.llm_calls = 1
        raise error


def test_failed_chat_usage_is_recorded(client_and_store, monkeypatch):
    client, store = client_and_store
    api_server.state.coordinator = FailedCoordinator()

    response = client.post("/api/chat", json={
        "question": "头痛",
        "session_id": "failed-usage",
        "patient_profile": "妈妈",
    })

    assert response.status_code == 502
    month = store.month_usage()
    assert month["usage"]["total_tokens"] == 123
    assert month["llm_calls"] == 1
    today = datetime.now().strftime("%Y-%m-%d")
    bucket = next(
        item for item in store.daily_usage(7)["days"]
        if item["date"] == today
    )
    assert bucket["usage"]["total_tokens"] == 123


def test_usage_events_merge_total_from_prompt_completion(tmp_path):
    store = SessionStore(str(tmp_path / "usage.db"))
    try:
        store.record_usage_event(
            "s1", "妈妈", {"prompt_tokens": 60, "completion_tokens": 40}, 2
        )
        month = store.month_usage()
        assert month["usage"]["total_tokens"] == 100
        assert month["llm_calls"] == 2
    finally:
        store.close()


def test_usage_month_percent_not_rounded_to_full(
    client_and_store, monkeypatch
):
    client, store = client_and_store
    monkeypatch.setenv("MEDIX_MONTHLY_TOKEN_BUDGET", "2000")
    store.record_usage_event("s1", "妈妈", {"total_tokens": 1999}, 1)

    data = client.get("/api/usage/month").json()

    assert data["exceeded"] is False
    assert data["percent"] == 99.9


def test_stream_claim_released_when_closed_before_task():
    source = (
        Path(__file__).resolve().parents[1] / "api_server.py"
    ).read_text(encoding="utf-8")
    start = source.index("async def event_stream()")

    assert "async def event_stream_inner" in source
    assert "_release_profile_claim(session_id, memory_profile)" in source[
        start:start + 800
    ]


def test_agent_done_reports_failed_state():
    root = Path(__file__).resolve().parents[1]
    server = (root / "api_server.py").read_text(encoding="utf-8")
    app = (root / "web" / "app.js").read_text(encoding="utf-8")

    assert '"failed": any(' in server
    assert "failed ? '未能完成' : '已出具意见'" in app
