"""CSV 导入的无效记录不能被算作"重复跳过"。"""
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


def _import(client, path, name, content):
    return client.post(
        path,
        files={"file": (name, content.encode("utf-8"), "text/csv")},
    )


def test_medication_import_counts_invalid_date_as_invalid(client_and_store):
    client, _store = client_and_store
    response = _import(
        client,
        "/api/medications/import",
        "medications.csv",
        "药物名称,开始日期\n药A,not-a-date\n",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 0
    assert body["skipped"] == 0
    assert body["invalid"] == 1


def test_metric_import_counts_future_measurement_as_invalid(client_and_store):
    client, _store = client_and_store
    response = _import(
        client,
        "/api/metrics/import",
        "metrics.csv",
        "测量时间,患者档案,指标,数值1,数值2,单位\n"
        "2099-01-01T08:00,妈妈,体重,60,,kg\n",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 0
    assert body["skipped"] == 0
    assert body["invalid"] == 1


def test_symptom_import_counts_invalid_date_as_invalid(client_and_store):
    client, _store = client_and_store
    response = _import(
        client,
        "/api/symptoms/import",
        "symptoms.csv",
        "记录时间,患者档案,症状,程度,备注\n"
        "2026-99-99,妈妈,头痛,5,\n",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 0
    assert body["skipped"] == 0
    assert body["invalid"] == 1


def test_reminder_import_skipped_only_counts_duplicates(client_and_store):
    client, _store = client_and_store
    response = _import(
        client,
        "/api/reminders/import",
        "reminders.csv",
        "到期日,内容,档案\n"
        "2026-12-01,复查血压,妈妈\n"
        "2026-02-30,无效日期,妈妈\n",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1
    assert body["skipped"] == 0
    assert body["invalid"] == 1
