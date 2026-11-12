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


def test_clear_done_button_visible_without_showing_done(client):
    index = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'id="reminders-clear-done"' in index
    assert "updateClearDoneButton" in app_js
    assert "params.set('include_done', 'true');" in app_js
    assert "if (!(els.remindersShowDone && els.remindersShowDone.checked))" in app_js
    assert "items = items.filter((item) => !item.done);" in app_js
    assert (
        "els.remindersShowDone.addEventListener('change', refreshReminderViews);"
        in app_js
    )


def test_reminder_calendar_respects_show_done(client):
    app_js = client.get("/static/app.js").text

    assert "const visible = (items || []).filter((item) => (" in app_js
    assert "els.remindersShowDone && els.remindersShowDone.checked" in app_js
    assert "|| !item.done);" in app_js


def test_reminder_focus_is_restored_after_rerender(client):
    app_js = client.get("/static/app.js").text

    assert "function captureReminderFocusLabel()" in app_js
    assert "function restoreReminderFocusLabel(label)" in app_js
    assert "const focusLabel = captureReminderFocusLabel();" in app_js
    assert "restoreReminderFocusLabel(focusLabel);" in app_js
    # 跳转/重绘后仍按 aria-label 找回同一操作按钮
    assert "node.getAttribute('aria-label') === label" in app_js


def test_reminder_load_failure_clears_previous_profile(client):
    app_js = client.get("/static/app.js").text

    start = app_js.index("async function loadReminders()")
    body = app_js[start:start + 1600]
    tail = body[body.index("catch (err)"):]

    assert "reminderItemsCache = [];" in tail
    assert "updateClearDoneButton([]);" in tail
    assert "refreshReminderViews();" in tail
