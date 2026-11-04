"""月度 Token 预算配置与用量的测试。"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import api_server
import memory
from core.budget import get_token_budget
from memory.session_store import SessionStore


def test_budget_disabled_without_config(monkeypatch):
    monkeypatch.delenv("MEDIX_MONTHLY_TOKEN_BUDGET", raising=False)
    assert get_token_budget() == 0


def test_budget_reads_positive_integer(monkeypatch):
    monkeypatch.setenv("MEDIX_MONTHLY_TOKEN_BUDGET", "100000")
    assert get_token_budget() == 100000


def test_budget_ignores_invalid_and_negative(monkeypatch):
    for value in ("bad", "0", "-5", "1e9"):
        monkeypatch.setenv("MEDIX_MONTHLY_TOKEN_BUDGET", value)
        assert get_token_budget() == 0


def test_month_usage_only_counts_current_month(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_exchange(
            "s1", "本月问题", "本月答复",
            metadata={
                "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
                "llm_calls": 1,
            },
        )
        store.save_exchange(
            "s2", "上个月问题", "上个月答复",
            metadata={
                "usage": {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
                "llm_calls": 2,
            },
        )
        last_month = (
            datetime.now().date().replace(day=1) - timedelta(days=1)
        ).isoformat()
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE messages SET created_at = ? WHERE session_id = 's2'",
                (f"{last_month}T12:00:00",),
            )

        data = store.month_usage()

        assert data["month"] == datetime.now().strftime("%Y-%m")
        assert data["usage"]["total_tokens"] == 10
        assert data["llm_calls"] == 1
        assert data["turns"] == 1
    finally:
        store.close()


def test_usage_month_api_with_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIX_MONTHLY_TOKEN_BUDGET", "100")
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "本月问题", "本月答复",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/usage/month")
    finally:
        store.close()

    assert response.status_code == 200
    data = response.json()
    assert data["budget"] == 100
    assert data["percent"] == 10.0
    assert data["exceeded"] is False
    assert data["usage"]["total_tokens"] == 10


def test_usage_month_api_without_budget(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_MONTHLY_TOKEN_BUDGET", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/usage/month")
    finally:
        store.close()

    data = response.json()
    assert data["budget"] == 0
    assert data["percent"] is None
    assert data["exceeded"] is False


def test_status_exposes_monthly_budget(monkeypatch):
    monkeypatch.setenv("MEDIX_MONTHLY_TOKEN_BUDGET", "500000")
    monkeypatch.setattr(api_server.state, "coordinator", None)
    client = TestClient(api_server.app)

    data = client.get("/api/status").json()

    assert data["monthly_token_budget"] == 500000
