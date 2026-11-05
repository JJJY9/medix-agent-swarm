"""PWA 静态资源与 Service Worker 的离线测试。"""
import re

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_manifest_is_served(client):
    response = client.get("/static/manifest.webmanifest")

    assert response.status_code == 200
    data = response.json()
    assert data["name"].startswith("MediX")
    assert data["start_url"] == "/"
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert {"192x192", "512x512"} <= sizes


def test_service_worker_is_served_at_root_scope(client):
    response = client.get("/sw.js")

    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/"
    assert "medix-shell-v2" in response.text
    assert "/api/" in response.text


def test_service_worker_install_is_resilient_and_has_timeout(client):
    sw = client.get("/sw.js").text

    assert "Promise.allSettled" in sw
    assert "oldCache.match(url)" in sw
    assert "NETWORK_TIMEOUT_MS" in sw
    assert "Promise.race([network, timeout])" in sw
    assert "if (!cached)" in sw
    assert "self.skipWaiting()" in sw
    assert "caches.match('/')" in sw


def test_app_js_keeps_offline_state_and_handles_sw_updates(client):
    app_js = client.get("/static/app.js").text

    assert re.search(
        r"\} catch \(err\) \{\s*if \(navigator\.onLine === false\) \{",
        app_js,
    )
    assert "OFFLINE_BANNER" in app_js
    assert "choice.outcome === 'accepted'" in app_js
    assert "installPromptUsed" in app_js
    assert "document.getElementById('sw-refresh')" in app_js
    assert "describeServiceWorker" in app_js


def test_app_js_reconciles_aborted_stream_and_guards_bulk_delete(client):
    app_js = client.get("/static/app.js").text

    assert "scheduleAbortReconcile" in app_js
    assert "baselineMessageId" in app_js
    assert "lastServerMessageId" in app_js
    assert "已停止接收本次会诊" in app_js
    assert "会诊进行中，暂不能批量删除会话" in app_js


def test_shell_accessibility_regions_and_focusable_scrollable_areas(client):
    index = client.get("/").text
    app_js = client.get("/static/app.js").text
    styles = client.get("/static/styles.css").text

    assert 'role="status" aria-live="polite"' in index
    assert 'tabindex="0"' in index
    assert "viewport-fit=cover" in index
    assert "isWindowNearBottom" in app_js
    assert "aria-invalid" in app_js
    assert ".voice[hidden]" in styles
    assert "previousFocus.focus()" in app_js
    assert "contribution-body" in app_js
    assert 'html[data-theme="dark"] .bp-grade-ok' in styles
    assert 'html[data-theme="dark"] .timeline-badge-consultation' in styles
    assert ".jump-latest[hidden]" in styles


def test_pwa_icons_are_png(client):
    for name in ("icon-192.png", "icon-512.png"):
        response = client.get(f"/static/icons/{name}")
        assert response.status_code == 200
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_manifest_ships_shortcuts(client):
    response = client.get("/static/manifest.webmanifest")

    assert response.status_code == 200
    data = response.json()
    urls = {item["url"] for item in data["shortcuts"]}
    assert "/?action=new" in urls
    assert "/?action=reminders" in urls
    assert "/?action=reminders&calendar=1" in urls
    assert "/?action=metrics" in urls


def test_index_links_manifest_and_registers_worker(client):
    index = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'rel="manifest"' in index
    assert "/static/icons/icon-192.png" in index
    assert "navigator.serviceWorker.register('/sw.js')" in app_js
