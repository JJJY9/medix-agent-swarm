"""用药“全部打卡”按钮的接线回归。"""
import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_log_all_button_and_function_are_wired(client):
    index = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'id="medication-log-all"' in index
    assert "async function logAllMedications()" in app_js
    assert "els.medicationLogAll?.addEventListener('click', logAllMedications)" in app_js


def test_log_all_only_targets_active_medications_without_today_dose(client):
    app_js = client.get("/static/app.js").text

    start = app_js.index("async function logAllMedications()")
    body = app_js[start:start + 2500]
    assert "item.active && !Number(item.today_count)" in body
    assert "为今天尚未打卡的" in body
    assert "medicationLogBusy" in body
    assert "已打卡 ${success} 种药物" in body
