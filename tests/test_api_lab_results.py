"""化验结果 API、异常标记与备份的离线测试。"""
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


def _at(days=0, hour=8):
    moment = datetime.now() + timedelta(days=days)
    return moment.replace(hour=hour, minute=0, second=0)


def test_lab_results_api_roundtrip(client_and_store):
    client, _store = client_and_store
    tested_at = _at(0, 9).isoformat(timespec="minutes")

    created = client.post("/api/lab-results", json={
        "profile": "妈妈",
        "panel": "肝功能",
        "item": "ALT",
        "value": 55,
        "unit": "U/L",
        "ref_low": 7,
        "ref_high": 40,
        "tested_at": tested_at,
        "lab_name": "市医院检验科",
        "note": "空腹",
    })
    assert created.status_code == 200
    item = created.json()["lab_result"]
    assert item["abnormal"] is True
    assert item["abnormal_high"] is True
    assert item["abnormal_low"] is False
    assert item["ref_text"] == "7–40"

    listed = client.get(
        "/api/lab-results", params={"profile": "妈妈"}
    ).json()["lab_results"]
    assert [row["id"] for row in listed] == [item["id"]]

    updated = client.patch(
        f"/api/lab-results/{item['id']}", json={"value": 30}
    )
    assert updated.status_code == 200
    updated_item = updated.json()["lab_result"]
    assert updated_item["abnormal"] is False
    assert updated_item["item"] == "ALT"

    deleted = client.delete(f"/api/lab-results/{item['id']}")
    missing = client.delete("/api/lab-results/99999")
    actions = [
        row["action"] for row in client.get("/api/audit").json()["actions"]
    ]

    assert deleted.status_code == 200
    assert missing.status_code == 404
    assert "lab.create" in actions
    assert "lab.update" in actions
    assert "lab.delete" in actions


def test_lab_result_validation(client_and_store):
    client, _store = client_and_store
    tested_at = _at(0).isoformat(timespec="minutes")

    blank = client.post("/api/lab-results", json={
        "item": "   ", "value": 1,
    })
    missing_value = client.post("/api/lab-results", json={"item": "ALT"})
    bad_value = client.post("/api/lab-results", json={
        "item": "ALT", "value": "abc",
    })
    future = client.post("/api/lab-results", json={
        "item": "ALT", "value": 20,
        "tested_at": _at(2).isoformat(timespec="minutes"),
    })
    bad_range = client.post("/api/lab-results", json={
        "item": "ALT", "value": 20, "ref_low": 40, "ref_high": 7,
    })
    bad_date = client.post("/api/lab-results", json={
        "item": "ALT", "value": 20, "tested_at": "not-a-date",
    })

    assert blank.status_code == 400
    assert missing_value.status_code == 422
    assert bad_value.status_code == 422
    assert future.status_code == 400
    assert bad_range.status_code == 400
    assert bad_date.status_code == 400
    assert client.get("/api/lab-results").json()["lab_results"] == []


def test_lab_filters_and_latest(client_and_store):
    client, _store = client_and_store
    early = _at(-2).isoformat(timespec="minutes")
    late = _at(-1).isoformat(timespec="minutes")
    client.post("/api/lab-results", json={
        "profile": "妈妈", "panel": "肝功能", "item": "ALT",
        "value": 50, "tested_at": early,
    })
    client.post("/api/lab-results", json={
        "profile": "妈妈", "panel": "肝功能", "item": "ALT",
        "value": 30, "tested_at": late,
    })
    client.post("/api/lab-results", json={
        "profile": "妈妈", "panel": "肾功能", "item": "肌酐",
        "value": 80, "unit": "umol/L", "tested_at": late,
    })
    client.post("/api/lab-results", json={
        "profile": "爸爸", "panel": "肝功能", "item": "ALT",
        "value": 20, "tested_at": late,
    })

    by_item = client.get(
        "/api/lab-results", params={"profile": "妈妈", "item": "ALT"}
    ).json()["lab_results"]
    by_panel = client.get(
        "/api/lab-results", params={"profile": "妈妈", "panel": "肾功能"}
    ).json()["lab_results"]
    latest = client.get(
        "/api/lab-results/latest", params={"profile": "妈妈"}
    ).json()["lab_results"]

    assert [row["value"] for row in by_item] == [30, 50]
    assert [row["item"] for row in by_panel] == ["肌酐"]
    assert {row["item"]: row["value"] for row in latest} == {
        "ALT": 30, "肌酐": 80,
    }


def test_lab_results_export_import_roundtrip(client_and_store):
    client, _store = client_and_store
    client.post("/api/lab-results", json={
        "profile": "妈妈",
        "panel": "肝功能",
        "item": "=ALT",
        "value": 55,
        "unit": "U/L",
        "ref_low": 7,
        "ref_high": 40,
        "tested_at": _at(0).isoformat(timespec="minutes"),
        "lab_name": "市医院",
        "note": "空腹",
    })

    export = client.get("/api/lab-results/export.csv")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0] == [
        "检测时间", "患者档案", "分组", "项目", "数值", "单位",
        "参考下限", "参考上限", "结果提示", "化验机构", "备注", "ID",
    ]
    assert rows[1][3] == "'=ALT"
    assert rows[1][4] == "55"
    assert rows[1][8] == "偏高"

    item = client.get("/api/lab-results").json()["lab_results"][0]
    client.delete(f"/api/lab-results/{item['id']}")
    imported = client.post(
        "/api/lab-results/import",
        files={"file": ("labs.csv", export.content, "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1
    restored = client.get("/api/lab-results").json()["lab_results"]
    assert restored[0]["item"] == "=ALT"
    assert restored[0]["value"] == 55
    assert restored[0]["ref_low"] == 7
    assert restored[0]["ref_high"] == 40
    assert restored[0]["abnormal"] is True


def test_lab_results_in_timeline_dossier_and_family(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    tested_at = _at(0).isoformat(timespec="minutes")
    client.post("/api/lab-results", json={
        "profile": "奶奶",
        "panel": "血常规",
        "item": "血红蛋白",
        "value": 95,
        "unit": "g/L",
        "ref_low": 115,
        "ref_high": 150,
        "tested_at": tested_at,
    })

    timeline = client.get(
        "/api/timeline", params={"profile": "奶奶", "type": "lab"}
    ).json()["events"]
    assert len(timeline) == 1
    assert timeline[0]["type"] == "lab"
    assert timeline[0]["title"] == "血红蛋白"
    assert "95" in timeline[0]["value"]
    assert "参考 115–150" in timeline[0]["value"]
    assert timeline[0]["alert"] is True

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "## 最近化验" in dossier.text
    assert "血红蛋白" in dossier.text
    assert "偏低" in dossier.text

    overview = client.get("/api/family/overview").json()
    grandma = {
        item["name"]: item for item in overview["profiles"]
    }["奶奶"]
    assert grandma["labs"]["count"] == 1
    assert grandma["labs"]["abnormal"] == 1
    assert "血红蛋白" in grandma["labs"]["items"][0]

    export = client.get("/api/family/overview/export.csv")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0][-1] == "化验异常"
    row = next(row for row in rows[1:] if row[0] == "奶奶")
    assert "异常 1 项" in row[-1]
    assert "血红蛋白" in row[-1]


def test_lab_results_backup_roundtrip_and_profile_rename(
    client_and_store, tmp_path
):
    client, store = client_and_store
    item = client.post("/api/lab-results", json={
        "profile": "妈妈",
        "panel": "肝功能",
        "item": "AST",
        "value": 35,
        "unit": "U/L",
        "ref_low": 13,
        "ref_high": 35,
        "tested_at": _at(0).isoformat(timespec="minutes"),
    }).json()["lab_result"]

    data = store.export_data()
    assert any(row["id"] == item["id"] for row in data["lab_results"])

    target = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = target.import_data(data)
        restored = target.list_lab_results()
    finally:
        target.close()

    assert result["lab_results"] == 1
    assert restored[0]["item"] == "AST"
    assert restored[0]["value"] == 35
    assert restored[0]["ref_low"] == 13
    assert restored[0]["ref_high"] == 35

    store.rename_profile("妈妈", "母亲")
    moved = store.list_lab_results("母亲")
    assert [row["id"] for row in moved] == [item["id"]]
