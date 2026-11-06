"""第六轮前端审计回归：档案隔离、频率解析、SW 安装与可访问性。"""
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


def test_interactions_use_main_profile_and_reset_allergies(client):
    app_js = _app_js(client)

    start = app_js.index("async function loadMedicationsIntoInteractions()")
    body = app_js[start:start + 600]
    assert "const profile = els.profileSelect.value || '';" in body
    assert "els.metricProfile.value || els.profileSelect.value" not in body

    open_start = app_js.index("function openCalc()")
    open_body = app_js[open_start:open_start + 800]
    assert "els.calcInteractionsAllergies.value" in open_body
    assert "context.allergies" in open_body


def test_every_other_day_frequency_is_two_days(client):
    app_js = _app_js(client)

    start = app_js.index("function frequencyRepeatDays(frequency)")
    body = app_js[start:start + 500]
    every_other = body.index("每隔1天")
    generic = body.index("const everyDays")
    assert every_other < generic


def test_service_worker_install_failure_is_propagated(client):
    sw = client.get("/sw.js").text

    assert "await caches.delete(CACHE_NAME);" in sw
    assert "throw error;" in sw


def test_clearing_context_clears_active_profile(client):
    app_js = _app_js(client)

    start = app_js.index("els.contextClear.addEventListener")
    body = app_js[start:start + 300]
    assert "setActiveProfile('');" in body


def test_client_risk_detection_respects_negation(client):
    app_js = _app_js(client)

    start = app_js.index("function detectRisk(text)")
    body = app_js[start:start + 1600]
    assert "negatedAt" in body
    assert "不属于|不是|并非" in body
    assert "高风险(?!人群|因素)" in body
    assert "低风险(?!人群|因素)" in body


def test_bp_extremes_are_not_paired_from_different_records(client):
    app_js = _app_js(client)

    assert "收缩压最高" in app_js
    assert "舒张压最低" in app_js
    assert "pair(v1.max, v2.max, formatStatNumber)" not in app_js
    assert "收缩压 ${formatStatNumber(v1.min)}" in app_js


def test_calendar_selection_restores_focus(client):
    app_js = _app_js(client)

    assert "cell.dataset.date = date;" in app_js
    start = app_js.index("cell.addEventListener('click'")
    body = app_js[start:start + 500]
    assert "focusTarget.focus();" in body


def test_rename_profile_refreshes_family_overview(client):
    app_js = _app_js(client)

    start = app_js.index("async function renamePatientProfile()")
    body = app_js[start:start + 1200]
    assert "await loadFamilyOverview();" in body
