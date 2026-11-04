"""会诊 HTML 报告导出的静态资源离线测试。"""
import re

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_app_js_ships_standalone_html_report(client):
    app_js = client.get("/static/app.js").text

    assert "function buildReportHtml" in app_js
    assert "function renderMarkdownLite" in app_js
    assert "function escapeHtml" in app_js
    assert "导出 HTML 报告" in app_js
    assert "medix-report-" in app_js
    assert "window.print()" in app_js


def test_app_js_ships_voice_input(client):
    app_js = client.get("/static/app.js").text

    assert "function setupVoiceInput" in app_js
    assert "function toggleVoice" in app_js
    assert "webkitSpeechRecognition" in app_js
    assert "voice-toggle" in app_js


def test_app_js_ships_reminder_badge_and_notifications(client):
    app_js = client.get("/static/app.js").text

    assert "function updateReminderBadge" in app_js
    assert "function refreshReminderBadge" in app_js
    assert "function enableReminderNotifications" in app_js
    assert "Notification.requestPermission" in app_js
    assert "reminders-count" in app_js


def test_app_js_ships_session_compare(client):
    app_js = client.get("/static/app.js").text

    assert "function openCompare" in app_js
    assert "function renderCompareColumn" in app_js
    assert "function compareStats" in app_js
    assert "history-bulk-compare" in app_js
    assert "compare-drawer" in app_js


def test_calc_forms_have_submit_buttons(client):
    html = client.get("/").text
    forms = re.findall(
        r'<form[^>]*class="calc-form"[^>]*>.*?</form>', html, re.S
    )
    assert forms
    for block in forms:
        form_id = re.search(r'id="([^"]+)"', block).group(1)
        assert 'type="submit"' in block, f"{form_id} 缺少提交按钮"


def test_app_js_ships_visit_summary(client):
    app_js = client.get("/static/app.js").text

    assert "function buildVisitSummaryHtml" in app_js
    assert "function exportVisitSummary" in app_js
    assert "medix-visit-summary-" in app_js
    assert "visit-summary" in app_js
