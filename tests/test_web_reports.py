"""会诊 HTML 报告导出的静态资源离线测试。"""
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
