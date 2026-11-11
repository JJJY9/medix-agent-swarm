"""复诊准备单打印版测试。"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_visit_prep_print_html(client_and_store):
    client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_metric("妈妈", "weight", 62, measured_at=today)
    store.add_symptom("妈妈", "头晕", 5, recorded_at=today)

    response = client.get(
        "/api/health/visit-prep/print",
        params={"profile": "妈妈", "days": 30},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    html = response.text
    assert "复诊准备单" in html
    assert "档案：妈妈" in html
    assert "不构成医疗建议" in html
    assert "可使用浏览器菜单打印" in html
    assert "头晕" in html


def test_visit_prep_print_escapes_names(client_and_store):
    client, store = client_and_store
    store.add_attachment(
        "妈妈", "other", 0, "<script>alert(1)</script>.pdf",
        "xss.pdf", "application/pdf", 10, "",
    )
    response = client.get(
        "/api/health/visit-prep/print", params={"profile": "妈妈"}
    )
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_visit_prep_print_requires_profile(client_and_store):
    client, _store = client_and_store
    response = client.get("/api/health/visit-prep/print")
    assert response.status_code == 400
    assert "档案" in response.json()["detail"]
