"""结构化过敏史 API、上下文合并与备份测试。"""
import asyncio
import csv
import io
from datetime import datetime, timedelta

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


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def test_allergies_api_roundtrip(client_and_store):
    client, _store = client_and_store

    created = client.post("/api/allergies", json={
        "profile": "妈妈",
        "substance": "青霉素",
        "reaction": "皮疹",
        "severity": "severe",
        "kind": "drug",
        "noted_at": "2020-05-01",
        "note": "曾出现全身荨麻疹",
    })
    assert created.status_code == 200
    item = created.json()["allergy"]
    assert item["severity"] == "severe"
    assert item["active"] is True

    listed = client.get(
        "/api/allergies", params={"profile": "妈妈"}
    ).json()["allergies"]
    assert [row["id"] for row in listed] == [item["id"]]

    updated = client.patch(
        f"/api/allergies/{item['id']}",
        json={"severity": "mild", "active": False},
    )
    assert updated.status_code == 200
    updated_item = updated.json()["allergy"]
    assert updated_item["severity"] == "mild"
    assert updated_item["active"] is False
    assert updated_item["substance"] == "青霉素"

    deleted = client.delete(f"/api/allergies/{item['id']}")
    missing = client.delete("/api/allergies/99999")
    actions = [
        row["action"] for row in client.get("/api/audit").json()["actions"]
    ]

    assert deleted.status_code == 200
    assert missing.status_code == 404
    assert "allergy.create" in actions
    assert "allergy.update" in actions
    assert "allergy.delete" in actions


def test_allergy_validation(client_and_store):
    client, _store = client_and_store
    blank = client.post("/api/allergies", json={"substance": "   "})
    bad_severity = client.post("/api/allergies", json={
        "substance": "青霉素", "severity": "unknown",
    })
    bad_kind = client.post("/api/allergies", json={
        "substance": "青霉素", "kind": "unknown",
    })
    future = client.post("/api/allergies", json={
        "substance": "青霉素", "noted_at": _date(2),
    })
    bad_date = client.post("/api/allergies", json={
        "substance": "青霉素", "noted_at": "not-a-date",
    })

    assert blank.status_code == 400
    assert bad_severity.status_code == 400
    assert bad_kind.status_code == 400
    assert future.status_code == 400
    assert bad_date.status_code == 400
    assert client.get("/api/allergies").json()["allergies"] == []


def test_allergy_filters(client_and_store):
    client, _store = client_and_store
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "青霉素", "kind": "drug",
    })
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "花生", "kind": "food",
    })
    inactive = client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "尘螨", "kind": "environment",
        "active": False,
    }).json()["allergy"]

    active = client.get(
        "/api/allergies", params={"profile": "妈妈", "active_only": True}
    ).json()["allergies"]
    food = client.get(
        "/api/allergies", params={"profile": "妈妈", "kind": "food"}
    ).json()["allergies"]
    search = client.get(
        "/api/allergies", params={"q": "青霉素"}
    ).json()["allergies"]

    assert {row["substance"] for row in active} == {"青霉素", "花生"}
    assert [row["substance"] for row in food] == ["花生"]
    assert [row["substance"] for row in search] == ["青霉素"]
    assert inactive["active"] is False


def test_allergy_csv_export_import_roundtrip(client_and_store):
    client, _store = client_and_store
    client.post("/api/allergies", json={
        "profile": "妈妈",
        "substance": "=青霉素",
        "reaction": "皮疹",
        "severity": "severe",
        "kind": "drug",
        "noted_at": "2020-05-01",
        "note": "荨麻疹",
    })

    export = client.get("/api/allergies/export.csv")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0] == [
        "过敏原", "患者档案", "过敏反应", "严重程度", "类型",
        "状态", "发现时间", "备注", "ID",
    ]
    assert rows[1][0] == "'=青霉素"
    assert rows[1][3] == "重度"

    item = client.get("/api/allergies").json()["allergies"][0]
    client.delete(f"/api/allergies/{item['id']}")
    imported = client.post(
        "/api/allergies/import",
        files={"file": ("allergies.csv", export.content, "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1
    restored = client.get("/api/allergies").json()["allergies"]
    assert restored[0]["substance"] == "=青霉素"
    assert restored[0]["severity"] == "severe"
    assert restored[0]["kind"] == "drug"
    assert restored[0]["note"] == "荨麻疹"


def test_allergy_backup_roundtrip_and_profile_rename(
    client_and_store, tmp_path
):
    client, store = client_and_store
    item = client.post("/api/allergies", json={
        "profile": "妈妈",
        "substance": "磺胺",
        "reaction": "皮疹",
        "severity": "moderate",
        "kind": "drug",
    }).json()["allergy"]

    data = store.export_data()
    assert any(row["id"] == item["id"] for row in data["allergies"])

    target = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = target.import_data(data)
        restored = target.list_allergies()
    finally:
        target.close()

    assert result["allergies"] == 1
    assert restored[0]["substance"] == "磺胺"
    assert restored[0]["reaction"] == "皮疹"

    store.rename_profile("妈妈", "母亲")
    moved = store.list_allergies("母亲")
    assert [row["id"] for row in moved] == [item["id"]]


def test_structured_allergies_merge_into_chat_context(
    client_and_store, monkeypatch
):
    _client, store = client_and_store
    store.add_allergy(
        "妈妈", "青霉素", "皮疹", "severe", "drug", True, "", ""
    )
    store.add_allergy(
        "妈妈", "花生", "嘴唇肿胀", "moderate", "food", True, "", ""
    )
    store.add_allergy(
        "妈妈", "尘螨", "", "mild", "environment", False, "", ""
    )

    merged = asyncio.run(api_server._attach_structured_allergies(
        {"allergies": "磺胺（皮疹）"}, "妈妈"
    ))
    assert "磺胺" in merged["allergies"]
    assert "青霉素" in merged["allergies"]
    assert "花生" in merged["allergies"]
    assert "尘螨" not in merged["allergies"]
    assert "重度" in merged["allergies"]

    empty = asyncio.run(api_server._attach_structured_allergies({}, ""))
    assert empty == {}


def test_allergies_in_dossier(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.post("/api/allergies", json={
        "profile": "奶奶",
        "substance": "阿司匹林",
        "reaction": "胃痛",
        "severity": "moderate",
        "kind": "drug",
    })
    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "## 过敏史" in dossier.text
    assert "阿司匹林" in dossier.text
    assert "胃痛" in dossier.text
