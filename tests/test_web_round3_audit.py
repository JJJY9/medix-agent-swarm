"""前端第三轮审计回归：会话切换竞态、错误恢复、失败态与通知。"""
import re

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def _app_js(client):
    response = client.get("/static/app.js")
    assert response.status_code == 200
    return response.text


def test_new_session_invalidates_pending_history_loads(client):
    app_js = _app_js(client)

    start = app_js.index("async function startNewSession()")
    body = app_js[start:start + 400]
    assert "openSessionSeq += 1" in body
    assert "function clearCurrentSession()" in app_js
    assert app_js.count("clearCurrentSession();") >= 3


def test_retry_reconciles_before_resending(client):
    app_js = _app_js(client)

    assert "async function restoreCurrentSession" in app_js
    assert re.search(
        r"const restored = await restoreCurrentSession\(", app_js
    )
    assert "retry.baselineMessageId" in app_js
    assert re.search(
        r"renderError\(\s*err\.message \|\| String\(err\),\s*"
        r"\{ question, askTurn, baselineMessageId \}",
        app_js,
    )
    assert app_js.count("scheduleAbortReconcile(sessionId, question, baselineMessageId);") >= 2


def test_purge_blocks_during_consultation_and_clears_deleted_session(client):
    app_js = _app_js(client)

    start = app_js.index("async function purgeOldSessions()")
    body = app_js[start:start + 900]
    assert "if (busy)" in body
    assert "暂不能清理旧会诊" in body

    tail = app_js[start:start + 3500]
    assert "/messages?limit=1" in tail
    assert "clearCurrentSession()" in tail


def test_visit_summary_reports_partial_failures(client):
    app_js = _app_js(client)

    assert "const failures = [];" in app_js
    assert "track(metricsRes, '健康指标')" in app_js
    assert "failures.map(escapeHtml).join('、')" in app_js
    assert "以下内容可能不完整" in app_js
    assert ".warn {" in app_js


def test_failed_health_loads_clear_previous_profile_data(client):
    app_js = _app_js(client)

    for marker in (
        "symptomItemsCache = [];",
        "medicationItemsCache = [];",
        "medicationLogItemsCache = [];",
        "症状记录加载失败",
        "用药清单加载失败",
        "打卡记录加载失败",
    ):
        assert marker in app_js

    assert re.search(
        r"if \(requestSeq !== metricTrendRequestSeq\) return;\s*"
        r"renderMetricTrend\(\[\], metric\);",
        app_js,
    )
    assert re.search(
        r"els\.metricOverviewGrid\.replaceChildren\(\);\s*"
        r"els\.metricOverview\.hidden = true;",
        app_js,
    )


def test_reminder_notifications_use_service_worker(client):
    app_js = _app_js(client)

    assert "registration.showNotification('MediX 随访提醒', options)" in app_js
    assert "navigator.serviceWorker.ready" in app_js
    assert "浏览器未能发送到期通知" in app_js


def test_symptom_search_keeps_unsaved_edit(client):
    app_js = _app_js(client)

    assert re.search(
        r"editingSymptomId\s*&& !symptomItemsCache\.some\(", app_js
    )


def test_terminal_cards_are_announced(client):
    app_js = _app_js(client)

    assert app_js.count("wrap.setAttribute('role', 'alert');") >= 2
    assert re.search(
        r"announceChat\('已停止生成'\);\s*"
        r"renderStopped\(",
        app_js,
    )
    assert re.search(
        r"announceChat\('会诊中断'\);\s*"
        r"renderError\(",
        app_js,
    )
