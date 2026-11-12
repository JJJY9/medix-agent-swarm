"""随访提醒“完成今日到期”按钮的接线回归。"""
import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_complete_due_button_and_function_are_wired(client):
    index = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'id="reminders-complete-due"' in index
    assert "async function completeDueReminders()" in app_js
    assert (
        "els.remindersCompleteDue?.addEventListener('click', completeDueReminders)"
        in app_js
    )


def test_complete_due_targets_only_due_or_overdue_pending_reminders(client):
    app_js = client.get("/static/app.js").text

    start = app_js.index("async function completeDueReminders()")
    body = app_js[start:start + 1600]
    assert "!item.done && String(item.due_at || '').slice(0, 10) <= today" in body
    assert "今天到期或已逾期的提醒" in body
    assert "reminderActionBusy" in body
    assert "已完成 ${success} 条提醒" in body


def test_snooze_due_button_and_function_are_wired(client):
    index = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'id="reminders-snooze-due"' in index
    assert "推迟逾期 1 天" in index
    assert "async function snoozeDueReminders()" in app_js
    assert (
        "els.remindersSnoozeDue?.addEventListener('click', snoozeDueReminders)"
        in app_js
    )
    assert "String(item.due_at || '').slice(0, 10) < today" in app_js
    assert "`/api/reminders/${item.id}/snooze?days=1`" in app_js
    assert "已推迟 ${success} 条逾期提醒" in app_js
    assert "推迟逾期 1 天（${overdueCount}）" in app_js
