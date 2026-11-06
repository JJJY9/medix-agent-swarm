"""CSV 导入模板：列名、BOM 与导出后可直接导入。"""
import csv
import io

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore

EXPECTED_HEADERS = {
    "metrics": ["测量时间", "患者档案", "指标", "数值1", "数值2", "单位", "备注"],
    "symptoms": ["记录时间", "患者档案", "症状", "程度", "备注"],
    "medications": [
        "药物名称", "剂量", "频次", "患者档案", "状态", "备注",
        "库存", "库存单位", "低库存阈值", "有效期",
    ],
    "reminders": ["到期日", "内容", "档案", "状态", "提前天数", "重复天数", "备注"],
}


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_templates_have_expected_headers_and_example(client_and_store):
    client, _store = client_and_store

    for kind, headers in EXPECTED_HEADERS.items():
        response = client.get(f"/api/templates/{kind}")

        assert response.status_code == 200, kind
        assert response.content.startswith(b"\xef\xbb\xbf")
        rows = list(csv.reader(
            io.StringIO(response.content.decode("utf-8-sig"))
        ))
        assert rows[0] == headers, kind
        assert len(rows[1]) == len(headers), kind


def test_unknown_template_returns_404(client_and_store):
    client, _store = client_and_store

    assert client.get("/api/templates/unknown").status_code == 404


def test_templates_can_be_imported(client_and_store):
    client, _store = client_and_store

    endpoints = {
        "metrics": "/api/metrics/import",
        "symptoms": "/api/symptoms/import",
        "medications": "/api/medications/import",
        "reminders": "/api/reminders/import",
    }
    for kind, endpoint in endpoints.items():
        template = client.get(f"/api/templates/{kind}").content
        response = client.post(
            endpoint,
            files={
                "file": ("template.csv", template, "text/csv"),
            },
        )
        assert response.status_code == 200, (kind, response.text)

    assert client.get("/api/metrics").json()["metrics"]
    assert client.get("/api/symptoms").json()["symptoms"]
    assert client.get("/api/medications").json()["medications"]
    assert client.get(
        "/api/reminders", params={"include_done": True}
    ).json()["reminders"]
