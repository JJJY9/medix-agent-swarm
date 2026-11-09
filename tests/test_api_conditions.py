"""慢病/健康问题清单 API、上下文与备份测试。"""
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


def test_conditions_api_roundtrip(client_and_store):
    client, _store = client_and_store
    created = client.post("/api/conditions", json={
        "profile": "妈妈",
        "name": "原发性高血压",
        "icd10": "i10",
        "diagnosed_at": "2020-01-01",
        "status": "active",
        "severity": "moderate",
        "note": "服药后控制良好",
    })
    assert created.status_code == 200
    item = created.json()["condition"]
    assert item["icd10"] == "I10"
    assert item["status"] == "active"

    listed = client.get(
        "/api/conditions", params={"profile": "妈妈"}
    ).json()["conditions"]
    assert [row["id"] for row in listed] == [item["id"]]

    updated = client.patch(
        f"/api/conditions/{item['id']}",
        json={"status": "controlled"},
    )
    assert updated.status_code == 200
    assert updated.json()["condition"]["status"] == "controlled"

    deleted = client.delete(f"/api/conditions/{item['id']}")
    missing = client.delete("/api/conditions/99999")
    actions = [
        row["action"] for row in client.get("/api/audit").json()["actions"]
    ]

    assert deleted.status_code == 200
    assert missing.status_code == 404
    assert "condition.create" in actions
    assert "condition.update" in actions
    assert "condition.delete" in actions


def test_condition_validation(client_and_store):
    client, _store = client_and_store
    blank = client.post("/api/conditions", json={"name": "   "})
    bad_status = client.post("/api/conditions", json={
        "name": "高血压", "status": "unknown",
    })
    bad_severity = client.post("/api/conditions", json={
        "name": "高血压", "severity": "unknown",
    })
    future = client.post("/api/conditions", json={
        "name": "高血压", "diagnosed_at": _date(2),
    })
    bad_date = client.post("/api/conditions", json={
        "name": "高血压", "diagnosed_at": "not-a-date",
    })

    assert blank.status_code == 400
    assert bad_status.status_code == 400
    assert bad_severity.status_code == 400
    assert future.status_code == 400
    assert bad_date.status_code == 400
    assert client.get("/api/conditions").json()["conditions"] == []


def test_condition_filters(client_and_store):
    client, _store = client_and_store
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "高血压", "status": "active",
    })
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "糖尿病", "status": "controlled",
    })
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "肺炎", "status": "resolved",
    })

    active = client.get(
        "/api/conditions", params={"profile": "妈妈", "active_only": True}
    ).json()["conditions"]
    resolved = client.get(
        "/api/conditions", params={"profile": "妈妈", "status": "resolved"}
    ).json()["conditions"]

    assert {row["name"] for row in active} == {"高血压", "糖尿病"}
    assert [row["name"] for row in resolved] == ["肺炎"]


def test_conditions_export_csv(client_and_store):
    client, _store = client_and_store
    client.post("/api/conditions", json={
        "profile": "妈妈",
        "name": "=高血压",
        "icd10": "I10",
        "diagnosed_at": "2020-01-01",
        "status": "controlled",
        "severity": "severe",
    })

    export = client.get("/api/conditions/export.csv")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0] == [
        "疾病/健康问题", "患者档案", "ICD-10", "确诊日期",
        "状态", "严重程度", "备注", "ID",
    ]
    assert rows[1][0] == "'=高血压"
    assert rows[1][4] == "已控制"
    assert rows[1][5] == "重度"


def test_conditions_merge_into_chat_context(
    client_and_store, monkeypatch
):
    _client, store = client_and_store
    store.add_condition(
        "妈妈", "高血压", "I10", "2020-01-01", "active", "moderate", ""
    )
    store.add_condition(
        "妈妈", "糖尿病", "E11", "", "controlled", "mild", ""
    )
    store.add_condition(
        "妈妈", "肺炎", "J18", "", "resolved", "moderate", ""
    )

    merged = asyncio.run(api_server._attach_structured_conditions(
        {"history": "既往体健"}, "妈妈"
    ))
    assert "既往体健" in merged["history"]
    assert "高血压" in merged["history"]
    assert "糖尿病" in merged["history"]
    assert "肺炎" not in merged["history"]
    assert "管理中" in merged["history"]

    empty = asyncio.run(
        api_server._attach_structured_conditions({}, "")
    )
    assert empty == {}


def test_conditions_in_dossier_and_family(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.post("/api/conditions", json={
        "profile": "奶奶",
        "name": "慢性肾脏病 3 期",
        "icd10": "N18.3",
        "diagnosed_at": "2021-05-01",
        "status": "active",
        "severity": "severe",
    })

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "## 慢病清单" in dossier.text
    assert "慢性肾脏病 3 期" in dossier.text
    assert "N18.3" in dossier.text
    assert "管理中" in dossier.text

    overview = client.get("/api/family/overview").json()
    grandma = {
        item["name"]: item for item in overview["profiles"]
    }["奶奶"]
    assert grandma["conditions"]["count"] == 1
    assert "慢性肾脏病 3 期" in grandma["conditions"]["names"]

    export = client.get("/api/family/overview/export.csv")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0][-2] == "慢病"
    row = next(row for row in rows[1:] if row[0] == "奶奶")
    assert "慢性肾脏病 3 期" in row[-2]


def test_conditions_backup_roundtrip_and_profile_rename(
    client_and_store, tmp_path
):
    client, store = client_and_store
    item = client.post("/api/conditions", json={
        "profile": "妈妈",
        "name": "2 型糖尿病",
        "icd10": "E11",
        "diagnosed_at": "2019-03-01",
        "status": "controlled",
        "severity": "moderate",
    }).json()["condition"]

    data = store.export_data()
    assert any(row["id"] == item["id"] for row in data["conditions"])

    target = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = target.import_data(data)
        restored = target.list_conditions()
    finally:
        target.close()

    assert result["conditions"] == 1
    assert restored[0]["name"] == "2 型糖尿病"
    assert restored[0]["icd10"] == "E11"
    assert restored[0]["status"] == "controlled"

    store.rename_profile("妈妈", "母亲")
    moved = store.list_conditions("母亲")
    assert [row["id"] for row in moved] == [item["id"]]
