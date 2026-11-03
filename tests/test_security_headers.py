"""安全响应头的离线测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setattr(api_server.state, "coordinator", None)
    yield


def assert_security_headers(response):
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "camera=()" in response.headers["permissions-policy"]
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_security_headers_on_status_and_index():
    client = TestClient(api_server.app)

    assert_security_headers(client.get("/api/status"))
    assert_security_headers(client.get("/"))
    assert_security_headers(client.get("/static/theme-init.js"))


def test_security_headers_on_unauthorized(monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "secret-token")
    client = TestClient(api_server.app)

    response = client.get("/api/sessions")

    assert response.status_code == 401
    assert_security_headers(response)


def test_docs_skip_csp_for_swagger():
    client = TestClient(api_server.app)

    response = client.get("/docs")

    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert response.headers["x-frame-options"] == "DENY"
