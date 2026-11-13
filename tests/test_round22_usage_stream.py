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


class EmptyAnswerCoordinator:
    worker_pool = []

    async def process(self, question, context=None, session_id=None, **kwargs):
        return {
            "answer": "",
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 5,
                "total_tokens": 55,
            },
            "llm_calls": 1,
        }


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


def test_daily_usage_profile_filter_includes_usage_events(tmp_path):
    """按档案筛选日用量时，失败调用的用量必须按档案命中而非被丢弃。"""
    store = SessionStore(str(tmp_path / "usage.db"))
    try:
        store.record_usage_event(
            "s1", "妈妈", {"total_tokens": 100}, 1
        )
        store.record_usage_event(
            "s2", "爸爸", {"total_tokens": 40}, 1
        )
        mom = store.daily_usage(7, False, "妈妈")
        assert mom["totals"]["usage"]["total_tokens"] == 100
        assert mom["totals"]["llm_calls"] == 1
        dad = store.daily_usage(7, False, "爸爸")
        assert dad["totals"]["usage"]["total_tokens"] == 40
        everything = store.daily_usage(7)
        assert everything["totals"]["usage"]["total_tokens"] == 140
    finally:
        store.close()


def test_empty_answer_chat_records_usage(client_and_store):
    client, store = client_and_store
    api_server.state.coordinator = EmptyAnswerCoordinator()

    response = client.post("/api/chat", json={
        "question": "头痛",
        "session_id": "empty-usage",
        "patient_profile": "妈妈",
    })

    assert response.status_code == 502
    month = store.month_usage()
    assert month["usage"]["total_tokens"] == 55
    assert month["llm_calls"] == 1


def test_rename_profile_moves_usage_events(tmp_path):
    store = SessionStore(str(tmp_path / "usage.db"))
    try:
        store.record_usage_event("s1", "妈妈", {"total_tokens": 70}, 1)

        assert store.rename_profile("妈妈", "母亲") == "母亲"

        renamed = store.daily_usage(7, False, "母亲")
        assert renamed["totals"]["usage"]["total_tokens"] == 70
        assert "母亲" in store.list_used_profile_names()
        assert "妈妈" not in store.list_used_profile_names()
    finally:
        store.close()


def test_frontend_import_payload_keeps_usage_events():
    app = (
        Path(__file__).resolve().parents[1] / "web" / "app.js"
    ).read_text(encoding="utf-8")

    assert "usage_events: data.usage_events" in app


def test_usage_month_percent_not_rounded_to_full(
    client_and_store, monkeypatch
):
    client, store = client_and_store
    monkeypatch.setenv("MEDIX_MONTHLY_TOKEN_BUDGET", "2000")
    store.record_usage_event("s1", "妈妈", {"total_tokens": 1999}, 1)

    data = client.get("/api/usage/month").json()

    assert data["exceeded"] is False
    assert data["percent"] == 99.9


def test_usage_events_export_and_import_roundtrip(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        source.record_usage_event(
            "s1", "妈妈",
            {"prompt_tokens": 60, "completion_tokens": 40}, 2,
        )
        payload = source.export_data()
        assert len(payload["usage_events"]) == 1

        first = target.import_data(payload)
        assert first["usage_events"] == 1
        assert target.month_usage()["usage"]["total_tokens"] == 100

        again = target.import_data(payload)
        assert again["usage_events"] == 0
        assert target.month_usage()["usage"]["total_tokens"] == 100
    finally:
        source.close()
        target.close()


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
