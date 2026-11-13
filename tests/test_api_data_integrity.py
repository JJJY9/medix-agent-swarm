"""API 数据完整性回归：CSV 往返、整数边界与孤立代理码位。"""
import json

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


def _import_csv(client, url, content):
    return client.post(
        url,
        files={"file": ("export.csv", content.encode("utf-8-sig"), "text/csv")},
    )


def _post_raw_json(client, url, payload):
    """用 ASCII 转义的 JSON 字节发送，才能表达 \\ud800 这类代理码位。"""
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    return client.post(
        url, content=body, headers={"Content-Type": "application/json"}
    )


def test_csv_restore_cell_only_strips_protective_apostrophe():
    assert api_server.csv_restore_cell("'-阿司匹林") == "-阿司匹林"
    assert api_server.csv_restore_cell("'+餐后") == "+餐后"
    assert api_server.csv_restore_cell("'=SUM(A1)") == "=SUM(A1)"
    assert api_server.csv_restore_cell("'@user") == "@user"
    # 真实以撇号开头的文本保持不变
    assert api_server.csv_restore_cell("'普通备注") == "'普通备注"
    assert api_server.csv_restore_cell("阿司匹林") == "阿司匹林"


def test_csv_safe_cell_roundtrips_apostrophe_data():
    for original in ("-阿司匹林", "+餐后", "=1+1", "@user", "'=真实撇号", "'普通"):
        exported = api_server.csv_safe_cell(original)
        assert api_server.csv_restore_cell(exported) == original


def test_medications_export_import_roundtrip_keeps_values(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/medications",
        json={
            "profile": "母亲",
            "name": "-阿司匹林",
            "dose": "+100mg",
            "frequency": "每日一次",
            "note": "=随餐",
        },
    )
    assert created.status_code == 200

    exported = client.get("/api/medications/export.csv")
    assert exported.status_code == 200
    assert "'-阿司匹林" in exported.text

    imported = _import_csv(
        client, "/api/medications/import", exported.content.decode("utf-8-sig")
    )
    assert imported.status_code == 200

    items = client.get("/api/medications").json()["medications"]
    names = [item["name"] for item in items]
    assert names.count("-阿司匹林") == 1
    assert all(not name.startswith("'") for name in names)
    assert any(item["note"] == "=随餐" for item in items)


def test_symptoms_export_import_roundtrip_keeps_values(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/symptoms",
        json={
            "profile": "父亲",
            "symptom": "-头痛",
            "severity": 4,
            "recorded_at": "2026-11-06T09:00:00",
            "note": "+饭后加重",
        },
    )
    assert created.status_code == 200

    exported = client.get("/api/symptoms/export.csv")
    assert exported.status_code == 200

    imported = _import_csv(
        client, "/api/symptoms/import", exported.content.decode("utf-8-sig")
    )
    assert imported.status_code == 200

    symptoms = client.get("/api/symptoms").json()["symptoms"]
    names = [item["symptom"] for item in symptoms]
    assert names.count("-头痛") == 1
    assert all(not name.startswith("'") for name in names)


def test_metrics_export_import_roundtrip_keeps_values(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/metrics",
        json={
            "profile": "父亲",
            "metric": "weight",
            "value1": 70.5,
            "measured_at": "2026-11-06T08:00:00",
            "note": "-空腹称重",
        },
    )
    assert created.status_code == 200

    exported = client.get("/api/metrics/export.csv")
    assert exported.status_code == 200

    imported = _import_csv(
        client, "/api/metrics/import", exported.content.decode("utf-8-sig")
    )
    assert imported.status_code == 200

    metrics = client.get("/api/metrics").json()["metrics"]
    notes = [item["note"] for item in metrics]
    assert notes.count("-空腹称重") == 1
    assert all(not note.startswith("'") for note in notes)


def test_reminders_export_import_roundtrip_keeps_values(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/reminders",
        json={
            "title": "-复诊",
            "due_at": "2026-12-01",
            "profile": "母亲",
            "note": "+带化验单",
        },
    )
    assert created.status_code == 200

    exported = client.get("/api/reminders/export.csv")
    assert exported.status_code == 200

    imported = _import_csv(
        client, "/api/reminders/import", exported.content.decode("utf-8-sig")
    )
    assert imported.status_code == 200

    reminders = client.get("/api/reminders", params={"include_done": True})
    titles = [item["title"] for item in reminders.json()["reminders"]]
    assert titles.count("-复诊") == 1
    assert all(not title.startswith("'") for title in titles)


def test_huge_offsets_are_clamped_instead_of_500(client_and_store):
    client, _store = client_and_store
    huge = 10**30

    for url in (
        "/api/sessions",
        "/api/metrics",
        "/api/medications",
        "/api/medication-logs",
        "/api/symptoms",
    ):
        response = client.get(url, params={"offset": huge})
        assert response.status_code == 200, (url, response.text)

    # 2^63-1 是 SQLite 可绑定的边界值
    assert client.get("/api/sessions", params={"offset": 2**63 - 1}).status_code == 200


def test_huge_path_ids_are_rejected_with_422(client_and_store):
    client, _store = client_and_store
    huge = 10**30

    requests = (
        ("delete", f"/api/metrics/{huge}"),
        ("delete", f"/api/medications/{huge}"),
        ("delete", f"/api/symptoms/{huge}"),
        ("delete", f"/api/medication-logs/{huge}"),
        ("delete", f"/api/reminders/{huge}"),
        ("post", f"/api/reminders/{huge}/complete"),
        ("post", f"/api/reminders/{huge}/reopen"),
        ("get", f"/api/medications/{huge}/logs"),
    )
    for method, url in requests:
        response = getattr(client, method)(url)
        assert response.status_code == 422, (url, response.text)
        assert response.json()["detail"]


def test_surrogate_session_id_returns_clean_422(client_and_store):
    client, _store = client_and_store

    response = _post_raw_json(
        client, "/api/chat", {"question": "你好", "session_id": "\ud800"}
    )

    assert response.status_code == 422
    body = response.text
    assert "\ud800" not in body
    assert response.json()["detail"]


def test_surrogate_payloads_never_crash_validation(client_and_store):
    client, _store = client_and_store

    payloads = (
        ("/api/chat", {"question": "你好", "patient_profile": "\ud800"}),
        ("/api/profiles", {"name": "\ud800", "context": {"age": 30}}),
        ("/api/symptoms", {"symptom": "\ud800", "severity": 3}),
        ("/api/medications", {"name": "\ud800"}),
        ("/api/reminders", {"title": "\ud800", "due_at": "2026-12-01"}),
    )
    for url, payload in payloads:
        response = _post_raw_json(client, url, payload)
        assert response.status_code == 422, (url, response.text)
        assert response.json()["detail"]


def test_surrogate_in_nested_import_payload_is_rejected(client_and_store):
    client, _store = client_and_store

    response = _post_raw_json(
        client,
        "/api/sessions/import",
        {"sessions": [{"id": "\ud800", "title": "x", "messages": []}]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]


def test_non_finite_numbers_in_payload_never_crash_validation(
    client_and_store,
):
    """原始 JSON 里的 NaN/Infinity 会让 422 回显再次编码失败变成 500。"""
    client, _store = client_and_store

    payloads = (
        ("/api/tools/bmi", {"height_cm": 170, "weight_kg": float("nan")}),
        ("/api/tools/bmi", {"height_cm": 170, "weight_kg": float("inf")}),
        (
            "/api/tools/phq9",
            {"answers": [1, 2, 3, 4, 5, 6, 7, 8, float("nan")]},
        ),
        (
            "/api/tools/egfr",
            {"age": 40, "sex": "male", "creatinine": float("inf")},
        ),
        ("/api/lab-results", {"item": "血钾", "value": float("nan")}),
        ("/api/symptoms", {"symptom": "头痛", "severity": float("nan")}),
    )
    for url, payload in payloads:
        response = _post_raw_json(client, url, payload)
        assert response.status_code == 422, (url, response.text)
        assert response.json()["detail"]
        assert "NaN" not in response.text, (url, response.text)
        assert "Infinity" not in response.text, (url, response.text)
        # 回显内容必须是标准 JSON，可以被严格模式重新编码
        json.dumps(response.json(), allow_nan=False)


def test_deeply_nested_payload_returns_422(client_and_store):
    """近千层嵌套曾让校验递归爆栈，把请求变成 500。"""
    client, _store = client_and_store

    for depth in (100, 990, 1500, 2000):
        body = '{"name":"x","context":' + "[" * depth + "]" * depth + "}"
        response = client.post(
            "/api/profiles",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, (depth, response.text[:200])
        assert response.json()["detail"], depth
        json.dumps(response.json(), allow_nan=False)
