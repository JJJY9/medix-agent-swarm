"""用药未服原因记录测试。"""
import csv
import io
import sqlite3
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


def _now():
    return datetime.now().isoformat(timespec="minutes")


def _days_ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat(
        timespec="minutes"
    )


def _medication(client, name="氨氯地平", profile="妈妈", **extra):
    return client.post("/api/medications", json={
        "profile": profile, "name": name, **extra,
    }).json()["medication"]


def test_skip_requires_valid_reason_and_keeps_stock(client_and_store):
    _client, store = client_and_store
    item = store.add_medication("妈妈", "氨氯地平", stock=10)
    skipped = store.add_medication_log(
        item["id"], skipped=True, skip_reason="forget"
    )
    assert skipped["skipped"] is True
    assert skipped["skip_reason_label"] == "忘记"
    assert skipped["stock_delta"] == 0
    assert store.get_medication(item["id"])["stock"] == 10
    assert store.add_medication_log(
        item["id"], skipped=True, skip_reason="unknown"
    ) is None

    taken = store.add_medication_log(item["id"])
    assert taken["skipped"] is False
    assert store.get_medication(item["id"])["stock"] == 9


def test_skip_is_not_counted_as_taken(client_and_store):
    _client, store = client_and_store
    item = store.add_medication("妈妈", "氨氯地平")
    store.add_medication_log(item["id"])
    store.add_medication_log(
        item["id"], skipped=True, skip_reason="side_effect"
    )
    listed = store.list_medications("妈妈", True, 100, 0, 7)[0]
    assert listed["today_count"] == 1
    assert listed["skipped_today"] == 1
    assert listed["skipped_range"] == 1
    assert listed["last_skip_reason"] == "side_effect"
    assert listed["last_skip_reason_label"] == "副作用"


def test_logs_include_skips_and_delete_keeps_stock(client_and_store):
    _client, store = client_and_store
    item = store.add_medication("妈妈", "氨氯地平", stock=5)
    store.add_medication_log(item["id"])
    skip = store.add_medication_log(
        item["id"], skipped=True, skip_reason="cost"
    )
    logs = store.list_medication_logs(item["id"])
    assert logs[0]["skipped"] is True
    assert logs[0]["skip_reason_label"] == "费用"
    assert store.delete_medication_log(skip["id"]) is True
    # 删除未服记录不应回补库存（打卡已扣 1，未服未扣）
    assert store.get_medication(item["id"])["stock"] == 4
    listed = store.list_medications("妈妈", True, 100, 0, 7)[0]
    assert listed["today_count"] == 1
    assert listed["skipped_today"] == 0


def test_api_records_skip_and_reports(client_and_store):
    client, _store = client_and_store
    item = _medication(client)
    client.post(f"/api/medications/{item['id']}/logs", json={})
    response = client.post(f"/api/medications/{item['id']}/skip", json={
        "reason": "forget", "note": "出差",
    })
    assert response.status_code == 200
    log = response.json()["log"]
    assert log["skipped"] is True
    assert log["skip_reason_label"] == "忘记"

    medication = client.get(
        "/api/medications", params={"profile": "妈妈"}
    ).json()["medications"][0]
    assert medication["today_count"] == 1
    assert medication["skipped_today"] == 1
    assert medication["last_skip_reason_label"] == "忘记"

    logs = client.get("/api/medication-logs").json()["logs"]
    assert logs[0]["skipped"] is True
    assert logs[0]["note"] == "出差"

    export = client.get(
        "/api/medications/logs/export.csv", params={"profile": "妈妈"}
    )
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0] == [
        "药物名称", "剂量", "服用时间", "患者档案",
        "状态", "未服原因", "备注",
    ]
    statuses = {row[4]: row[5] for row in rows[1:]}
    assert statuses["未服"] == "忘记"
    assert statuses["已服"] == ""


def test_api_rejects_invalid_reason_and_unknown_medication(client_and_store):
    client, _store = client_and_store
    item = _medication(client)
    invalid = client.post(
        f"/api/medications/{item['id']}/skip",
        json={"reason": "unknown"},
    )
    assert invalid.status_code == 422
    missing = client.post(
        "/api/medications/999999/skip", json={"reason": "forget"}
    )
    assert missing.status_code == 404


def test_weekly_report_and_insights_include_skips(client_and_store):
    client, _store = client_and_store
    item = _medication(client, name="氨氯地平", frequency="每日一次")
    client.post(f"/api/medications/{item['id']}/logs", json={})
    for reason, days in (("forget", 3), ("forget", 2), ("side_effect", 1)):
        client.post(
            f"/api/medications/{item['id']}/skip",
            json={"reason": reason, "skipped_at": _days_ago(days)},
        )
    # 上一周期的未服记录用于 prev_skips
    client.post(
        f"/api/medications/{item['id']}/skip",
        json={"reason": "cost", "skipped_at": _days_ago(10)},
    )

    weekly = client.get(
        "/api/reports/weekly", params={"profile": "妈妈"}
    ).json()
    medications = weekly["medications"]
    assert medications["skips"] == 3
    assert medications["prev_skips"] == 1
    assert medications["doses"] == 1
    assert medications["adherence"]["taken"] == 1
    assert medications["skip_reasons"][0] == {
        "reason": "forget", "label": "忘记", "count": 2,
    }
    assert medications["skip_items"][0]["skip_reason"] == "side_effect"

    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()["insights"]
    skip_insight = next(
        item for item in insights if item["key"] == "medication_skips"
    )
    assert skip_insight["level"] == "warning"
    # 30 天窗口包含上一周期的 1 次未服
    assert "未服 4 次" in skip_insight["title"]
    assert "忘记 2 次" in skip_insight["detail"]


def test_family_insight_lists_members(client_and_store):
    client, _store = client_and_store
    mom = _medication(client, "氨氯地平", "妈妈")
    dad = _medication(client, "二甲双胍", "爸爸")
    client.post(
        f"/api/medications/{mom['id']}/skip", json={"reason": "forget"}
    )
    client.post(
        f"/api/medications/{dad['id']}/skip", json={"reason": "cost"}
    )
    insights = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()["insights"]
    skip_insight = next(
        item for item in insights if item["key"] == "medication_skips"
    )
    assert "妈妈" in skip_insight["detail"]
    assert "爸爸" in skip_insight["detail"]


def test_timeline_marks_skip_as_not_taken(client_and_store):
    client, _store = client_and_store
    item = _medication(client)
    client.post(f"/api/medications/{item['id']}/skip", json={
        "reason": "inconvenient",
    })
    events = client.get(
        "/api/timeline", params={"profile": "妈妈", "type": "medication"}
    ).json()["events"]
    assert events[0]["value"].startswith("未服")
    assert "不方便" in events[0]["value"]


def test_migration_from_old_log_schema(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            dose TEXT NOT NULL DEFAULT '',
            frequency TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            stock REAL,
            stock_unit TEXT NOT NULL DEFAULT '',
            low_stock_threshold REAL NOT NULL DEFAULT 0,
            expiry_date TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL DEFAULT '',
            stopped_at TEXT NOT NULL DEFAULT '',
            stop_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE medication_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medication_id INTEGER NOT NULL,
            profile TEXT NOT NULL DEFAULT '',
            taken_at TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        INSERT INTO medications(profile, name, created_at, updated_at)
        VALUES ('妈妈', '氨氯地平', '2026-01-01', '2026-01-01');
        INSERT INTO medication_logs(
            medication_id, profile, taken_at, note, created_at
        ) VALUES (1, '妈妈', '2026-01-02', '', '2026-01-02');
        """
    )
    conn.commit()
    conn.close()

    store = SessionStore(str(path))
    try:
        logs = store.list_medication_logs(1)
        assert logs[0]["skipped"] is False
        skipped = store.add_medication_log(
            1, skipped=True, skip_reason="forget"
        )
        assert skipped["skipped"] is True
    finally:
        store.close()


def test_backup_roundtrip_preserves_skips(client_and_store, tmp_path):
    _client, store = client_and_store
    item = store.add_medication("妈妈", "氨氯地平")
    taken_at = _now()
    store.add_medication_log(item["id"], taken_at=taken_at, note="同刻")
    store.add_medication_log(
        item["id"], taken_at=taken_at, note="同刻",
        skipped=True, skip_reason="worry_side_effect",
    )
    payload = store.export_data()

    other = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = other.import_data(payload)
        # 同一时间同一备注的已服与未服都要保留
        assert result["medication_logs"] == 2
        restored_med = other.list_medications("妈妈", True, 100, 0, 7)[0]
        logs = other.list_medication_logs(restored_med["id"])
        assert sorted(log["skipped"] for log in logs) == [False, True]
        skipped = next(log for log in logs if log["skipped"])
        assert skipped["skip_reason"] == "worry_side_effect"
    finally:
        other.close()

    # 旧备份没有 skipped/skip_reason 字段时按已服处理
    legacy = {
        key: value
        for key, value in payload.items()
        if key != "checksum"
    }
    legacy["medication_logs"] = [
        {
            key: value
            for key, value in log.items()
            if key not in ("skipped", "skip_reason")
        }
        for log in payload["medication_logs"]
    ]
    legacy_store = SessionStore(str(tmp_path / "legacy.db"))
    try:
        legacy_result = legacy_store.import_data(legacy)
        assert legacy_result["medication_logs"] == 2
        legacy_med = legacy_store.list_medications(
            "妈妈", True, 100, 0, 7
        )[0]
        legacy_logs = legacy_store.list_medication_logs(legacy_med["id"])
        assert all(log["skipped"] is False for log in legacy_logs)
    finally:
        legacy_store.close()

    # 非法原因归一为「其他」
    invalid = {
        key: value
        for key, value in payload.items()
        if key != "checksum"
    }
    invalid["medication_logs"] = [
        (
            {**log, "skip_reason": "weird"}
            if log.get("skipped") else log
        )
        for log in payload["medication_logs"]
    ]
    invalid_store = SessionStore(str(tmp_path / "invalid.db"))
    try:
        invalid_store.import_data(invalid)
        invalid_med = invalid_store.list_medications(
            "妈妈", True, 100, 0, 7
        )[0]
        invalid_logs = invalid_store.list_medication_logs(
            invalid_med["id"]
        )
        skipped = next(log for log in invalid_logs if log["skipped"])
        assert skipped["skip_reason"] == "other"
    finally:
        invalid_store.close()


def test_future_skip_does_not_hijack_last_reason(client_and_store):
    _client, store = client_and_store
    item = store.add_medication("妈妈", "氨氯地平")
    store.add_medication_log(
        item["id"], skipped=True, skip_reason="forget"
    )
    future = (
        datetime.now() + timedelta(days=2)
    ).isoformat(timespec="minutes")
    with store._lock, store._conn:
        store._conn.execute(
            """
            INSERT INTO medication_logs(
                medication_id, profile, taken_at, note, stock_delta,
                skipped, skip_reason, created_at
            ) VALUES (?, ?, ?, '', 0, 1, ?, ?)
            """,
            (item["id"], "妈妈", future, "cost", _now()),
        )
    listed = store.list_medications("妈妈", True, 100, 0, 7)[0]
    assert listed["last_skip_reason"] == "forget"
    assert listed["skipped_range"] == 1


def test_family_overview_counts_skip_as_activity(client_and_store):
    client, _store = client_and_store
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 60,
        "measured_at": _days_ago(1),
    })
    item = _medication(client)
    client.post(f"/api/medications/{item['id']}/skip", json={
        "reason": "forget",
    })
    mom = next(
        row for row in client.get(
            "/api/family/overview", params={"days": 7}
        ).json()["profiles"]
        if row["name"] == "妈妈"
    )
    assert mom["recorded_today"] is True
    assert mom["medications"]["skipped_today"] == 1
    assert mom["last_activity"][:10] == datetime.now().strftime("%Y-%m-%d")


def test_delete_medication_redacts_skip_audit(client_and_store):
    client, _store = client_and_store
    item = _medication(client)
    client.post(f"/api/medications/{item['id']}/skip", json={
        "reason": "side_effect", "skipped_at": _days_ago(1),
    })
    assert client.delete(
        f"/api/medications/{item['id']}"
    ).status_code == 200
    audit = client.get("/api/audit", params={"limit": 100}).json()
    entries = audit.get("actions") or []
    for entry in entries:
        target = str(entry.get("target") or "")
        assert "side_effect" not in target
