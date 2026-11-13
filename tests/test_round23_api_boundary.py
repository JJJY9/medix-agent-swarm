"""第二十三轮审查：导入边界与切换档案后的统计残留。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import MAX_SQLITE_INT, SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_import_usage_events_clamps_token_bounds(tmp_path):
    store = SessionStore(str(tmp_path / "usage.db"))
    try:
        result = store.import_data({
            "usage_events": [{
                "session_id": "s1",
                "profile": "妈妈",
                "prompt_tokens": 2**63,
                "completion_tokens": -5,
                "llm_calls": 2**63,
            }],
        })
        events = store.list_usage_events()

        assert result["usage_events"] == 1
        assert len(events) == 1
        assert events[0]["prompt_tokens"] == MAX_SQLITE_INT
        assert events[0]["completion_tokens"] == 0
        assert events[0]["llm_calls"] == MAX_SQLITE_INT
        assert events[0]["total_tokens"] == MAX_SQLITE_INT
    finally:
        store.close()


def test_import_endpoint_handles_usage_overflow(client_and_store):
    client, _store = client_and_store

    response = client.post("/api/sessions/import", json={
        "sessions": [],
        "usage_events": [{
            "session_id": "s", "profile": "妈妈",
            "prompt_tokens": 2**63, "llm_calls": 1,
        }],
    })

    assert response.status_code == 200, response.text
    assert response.json()["usage_events"] == 1


def test_metric_summary_failure_clears_previous_profile_values():
    app = (
        Path(__file__).resolve().parents[1] / "web" / "app.js"
    ).read_text(encoding="utf-8")

    summary = app[app.index("async function loadMetricSummary"):]
    summary = summary[:summary.index("function renderMetricWeekly")]
    assert "if (!res.ok) return;" not in summary
    assert "metricStatGrid.replaceChildren()" in summary

    weekly = app[app.index("async function loadMetricWeekly"):]
    weekly = weekly[:weekly.index("async function loadMetricOverview")]
    assert "if (!res.ok) return;" not in weekly
    assert "metricWeeklyList.replaceChildren()" in weekly
