"""PWA 静态资源与 Service Worker 的离线测试。"""
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
    assert "medix-shell-v1" in response.text
    assert "/api/" in response.text


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
    assert "/?action=metrics" in urls


def test_index_links_manifest_and_registers_worker(client):
    index = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'rel="manifest"' in index
    assert "/static/icons/icon-192.png" in index
    assert "navigator.serviceWorker.register('/sw.js')" in app_js
