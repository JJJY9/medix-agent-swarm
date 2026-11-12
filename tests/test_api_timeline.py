"""健康时间线 API 的离线测试。"""
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


def test_timeline_merges_and_filters(client_and_store):
    client, _store = client_and_store
    now = datetime.now()
    bp_time = (now - timedelta(minutes=3)).isoformat(timespec="minutes")
    symptom_time = (now - timedelta(minutes=2)).isoformat(timespec="minutes")
    log_time = (now - timedelta(minutes=1)).isoformat(timespec="minutes")
    older_time = (now - timedelta(days=10)).isoformat(timespec="minutes")

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 85, "measured_at": bp_time,
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕",
        "severity": 4, "recorded_at": symptom_time,
    })
    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平", "dose": "5 mg",
    }).json()["medication"]
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": log_time, "note": "早餐后"},
    )
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight",
        "value1": 60, "measured_at": older_time,
    })
    client.post("/api/metrics", json={
        "profile": "父亲", "metric": "weight",
        "value1": 70, "measured_at": now.isoformat(timespec="minutes"),
    })

    timeline = client.get(
        "/api/timeline", params={"profile": "妈妈"}
    ).json()
    events = timeline["events"]

    assert timeline["total"] == 4
    assert [event["type"] for event in events] == [
        "medication", "symptom", "metric", "metric"
    ]
    assert events[0]["title"] == "氨氯地平"
    assert events[0]["value"] == "5 mg · 打卡"
    assert events[0]["note"] == "早餐后"
    assert events[1]["value"] == "4/10"
    assert events[2]["title"] == "血压"
    assert events[2]["value"] == "130/85 mmHg"
    assert events[3]["value"] == "60 kg"

    recent = client.get(
        "/api/timeline", params={"profile": "妈妈", "days": 7}
    ).json()
    assert recent["total"] == 3
    assert all(
        event["title"] != "体重" or event["value"] != "60 kg"
        for event in recent["events"]
    )

    father = client.get(
        "/api/timeline", params={"profile": "父亲"}
    ).json()
    assert father["total"] == 1
    assert father["events"][0]["title"] == "体重"

    limited = client.get(
        "/api/timeline", params={"profile": "妈妈", "limit": 2}
    ).json()
    assert len(limited["events"]) == 2
    assert limited["total"] == 4

    family = client.get("/api/timeline").json()
    assert family["total"] == 5
    assert family["events"][0]["profile"] == "父亲"


def test_timeline_consultation_risk(client_and_store):
    client, store = client_and_store

    store.save_exchange(
        "risk-timeline", "头晕怎么办", "风险等级：中危，建议复测血压。"
    )

    events = client.get("/api/timeline").json()["events"]
    consultation = events[0]

    assert consultation["type"] == "consultation"
    assert consultation["risk"] == "medium"
    assert "中危" in consultation["value"]


def test_timeline_consultation_without_risk(client_and_store):
    client, store = client_and_store

    store.save_exchange("plain-session", "普通咨询", "建议多休息、多喝水。")

    events = client.get("/api/timeline").json()["events"]
    consultation = events[0]

    assert consultation["type"] == "consultation"
    assert consultation["risk"] == ""
    assert "中危" not in consultation["value"]


def test_timeline_filters_single_day(client_and_store):
    client, _store = client_and_store

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 85, "measured_at": "2026-10-02T08:00",
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕",
        "severity": 3, "recorded_at": "2026-10-03T09:00",
    })

    day = client.get(
        "/api/timeline", params={"profile": "妈妈", "date": "2026-10-02"}
    ).json()
    assert day["date"] == "2026-10-02"
    assert day["total"] == 1
    assert day["events"][0]["type"] == "metric"

    empty = client.get(
        "/api/timeline", params={"profile": "妈妈", "date": "2026-10-04"}
    ).json()
    assert empty["total"] == 0

    exported = client.get(
        "/api/timeline/export.csv",
        params={"profile": "妈妈", "date": "2026-10-02"},
    )
    rows = list(
        csv.reader(io.StringIO(exported.content.decode("utf-8-sig")))
    )
    assert len(rows) == 2
    assert rows[1][1] == "指标"

    invalid = client.get(
        "/api/timeline", params={"date": "2026-10-xx"}
    )
    assert invalid.status_code == 400


def test_timeline_includes_attachments(client_and_store):
    client, store = client_and_store
    store.add_attachment(
        "妈妈", "visit", 0, "出院小结.pdf", "a.pdf",
        "application/pdf", 100, "心内科复查",
    )
    store.add_attachment(
        "爸爸", "visit", 0, "爸爸资料.pdf", "b.pdf",
        "application/pdf", 100, "",
    )
    data = client.get(
        "/api/timeline",
        params={"profile": "妈妈", "type": "attachment"},
    ).json()
    assert data["total"] == 1
    event = data["events"][0]
    assert event["type"] == "attachment"
    assert event["title"] == "出院小结.pdf"
    assert "就诊资料" in event["value"]
    assert "心内科复查" in event["value"]
    assert event["kind"] == "visit"
    assert event["mime"] == "application/pdf"


def test_timeline_csv_export(client_and_store):
    client, _store = client_and_store
    now = datetime.now().isoformat(timespec="minutes")

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 85, "measured_at": now,
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "=SUM(A1:A2)",
        "severity": 4, "recorded_at": now,
    })

    export = client.get(
        "/api/timeline/export.csv",
        params={"profile": "妈妈", "days": 7},
    )
    text = export.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))

    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    assert rows[0] == [
        "时间", "类型", "项目", "数值", "患者档案", "备注", "超标",
    ]
    assert {row[1] for row in rows[1:]} == {"指标", "症状"}
    assert any(row[2].startswith("'=SUM") for row in rows[1:])
    assert all(row[6] == "" for row in rows[1:])


def test_timeline_marks_over_target_metrics(client_and_store):
    client, _store = client_and_store
    now = datetime.now()
    high = (now - timedelta(minutes=2)).isoformat(timespec="minutes")
    normal = (now - timedelta(minutes=1)).isoformat(timespec="minutes")

    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 135, "value2": 85,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 140, "value2": 95, "measured_at": high,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 120, "value2": 80, "measured_at": normal,
    })

    events = client.get(
        "/api/timeline", params={"profile": "妈妈"}
    ).json()["events"]

    assert events[0]["value"] == "120/80 mmHg"
    assert events[0]["alert"] is False
    assert events[1]["value"] == "140/95 mmHg"
    assert events[1]["alert"] is True

    exported = client.get(
        "/api/timeline/export.csv", params={"profile": "妈妈"}
    )
    rows = list(
        csv.reader(io.StringIO(exported.content.decode("utf-8-sig")))
    )
    alert_rows = [row for row in rows[1:] if row[6]]
    assert len(alert_rows) == 1
    assert alert_rows[0][3] == "140/95 mmHg"


def test_timeline_csv_export_follows_filters(client_and_store):
    client, _store = client_and_store
    now = datetime.now()
    metric_at = (now - timedelta(minutes=3)).isoformat(timespec="minutes")
    symptom_at = (now - timedelta(minutes=2)).isoformat(timespec="minutes")

    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 135, "value2": 85,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 140, "value2": 95, "measured_at": metric_at,
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕",
        "severity": 3, "recorded_at": symptom_at,
    })

    def read_rows(**params):
        response = client.get(
            "/api/timeline/export.csv",
            params={"profile": "妈妈", **params},
        )
        return list(
            csv.reader(io.StringIO(response.content.decode("utf-8-sig")))
        )

    symptom_rows = read_rows(type="symptom")
    assert [row[1] for row in symptom_rows[1:]] == ["症状"]

    alert_rows = read_rows(only_alert="true")
    assert len(alert_rows) == 2
    assert alert_rows[1][6] == "是"

    search_rows = read_rows(q="头晕")
    assert len(search_rows) == 2
    assert search_rows[1][1] == "症状"


def test_timeline_includes_consultations(client_and_store):
    client, store = client_and_store

    store.save_exchange("session-mom", "妈妈最近头晕怎么办", "建议监测血压。")
    store.set_session_profile("session-mom", "妈妈")
    store.save_exchange("session-empty", "没有档案的会诊", "已记录。")

    timeline = client.get("/api/timeline").json()
    consultation = [
        event for event in timeline["events"]
        if event["type"] == "consultation"
    ]
    assert len(consultation) == 2
    assert consultation[0]["id"].startswith("session-")
    assert consultation[0]["value"] == "会诊 · 2 条消息"

    mom = client.get(
        "/api/timeline", params={"profile": "妈妈"}
    ).json()
    assert [event["type"] for event in mom["events"]] == ["consultation"]
    assert mom["events"][0]["title"] == "妈妈最近头晕怎么办"
    assert mom["events"][0]["profile"] == "妈妈"

    unassigned = client.get(
        "/api/timeline", params={"profile": ""}
    ).json()
    assert [event["id"] for event in unassigned["events"]] == ["session-empty"]

    exported = client.get(
        "/api/timeline/export.csv", params={"profile": "妈妈"}
    )
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert rows[1][1] == "会诊"
    assert rows[1][2] == "妈妈最近头晕怎么办"


def test_timeline_date_keeps_sessions_past_newer_limit(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    target_day = (now - timedelta(days=20)).strftime("%Y-%m-%d")
    target_at = f"{target_day}T09:00:00"
    recent_at = now.isoformat(timespec="seconds")

    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO sessions("
            "id,title,created_at,updated_at,favorite,archived,tags,profile"
            ") VALUES (?,?,?,?,0,0,'[]','妈妈')",
            ("target-session", "目标日会诊", target_at, target_at),
        )
        store._conn.execute(
            "INSERT INTO messages(session_id,role,content,created_at) "
            "VALUES (?,?,?,?)",
            ("target-session", "assistant", "建议复查。", target_at),
        )
        store._conn.executemany(
            "INSERT INTO sessions("
            "id,title,created_at,updated_at,favorite,archived,tags,profile"
            ") VALUES (?,?,?,?,0,0,'[]','妈妈')",
            [
                (f"newer-{index}", "较新会诊", recent_at, recent_at)
                for index in range(501)
            ],
        )
        store._conn.executemany(
            "INSERT INTO messages(session_id,role,content,created_at) "
            "VALUES (?,?,?,?)",
            [
                (f"newer-{index}", "assistant", "建议。", recent_at)
                for index in range(501)
            ],
        )

    data = client.get(
        "/api/timeline", params={"profile": "妈妈", "date": target_day}
    ).json()
    consultations = [
        event for event in data["events"]
        if event["type"] == "consultation"
    ]
    assert [event["id"] for event in consultations] == ["target-session"]


def test_timeline_export_filters_before_limit(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    old_at = (now - timedelta(days=20)).isoformat(timespec="seconds")
    recent_at = now.isoformat(timespec="minutes")

    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO sessions("
            "id,title,created_at,updated_at,favorite,archived,tags,profile"
            ") VALUES (?,?,?,?,0,0,'[]','妈妈')",
            ("old-session", "旧会诊", old_at, old_at),
        )
        store._conn.execute(
            "INSERT INTO messages(session_id,role,content,created_at) "
            "VALUES (?,?,?,?)",
            ("old-session", "assistant", "建议复查。", old_at),
        )
        store._conn.executemany(
            "INSERT INTO health_metrics("
            "profile,metric,value1,value2,unit,measured_at,note,created_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            [
                ("妈妈", "weight", 60, None, "kg", recent_at, "", recent_at)
                for _ in range(501)
            ],
        )

    response = client.get(
        "/api/timeline/export.csv",
        params={"profile": "妈妈", "days": 30, "type": "consultation"},
    )
    rows = list(
        csv.reader(io.StringIO(response.content.decode("utf-8-sig")))
    )

    assert len(rows) == 2
    assert rows[1][1] == "会诊"
    assert rows[1][2] == "旧会诊"


def test_timeline_filters_high_risk_consultations(client_and_store):
    client, store = client_and_store

    store.save_exchange("r-low", "头晕", "风险等级：低危。")
    store.save_exchange("r-high", "胸痛", "风险等级：高危。")
    store.save_exchange(
        "r-emg", "剧烈胸痛", "风险等级：**紧急**，立即就医。"
    )

    data = client.get(
        "/api/timeline", params={"only_risk": "true"}
    ).json()
    assert {event["id"] for event in data["events"]} == {
        "r-high", "r-emg",
    }

    export = client.get(
        "/api/timeline/export.csv", params={"only_risk": "true"}
    )
    rows = list(
        csv.reader(io.StringIO(export.content.decode("utf-8-sig")))
    )
    assert len(rows) == 3
    assert {row[2] for row in rows[1:]} == {"胸痛", "剧烈胸痛"}


def test_timeline_export_supports_more_than_2000_events(client_and_store):
    client, store = client_and_store
    now = datetime.now().isoformat(timespec="seconds")
    total = 2005

    with store._lock, store._conn:
        store._conn.executemany(
            "INSERT INTO health_metrics("
            "profile,metric,value1,value2,unit,measured_at,note,created_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            [
                ("妈妈", "weight", 60, None, "kg", now, "", now)
                for _ in range(total)
            ],
        )

    export = client.get(
        "/api/timeline/export.csv",
        params={"profile": "妈妈", "days": 30},
    )
    rows = list(
        csv.reader(io.StringIO(export.content.decode("utf-8-sig")))
    )

    assert export.status_code == 200
    assert len(rows) == total + 1


def test_timeline_date_finds_attachment_beyond_newest_page(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    old_day = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    old_at = f"{old_day}T09:00:00"
    recent_at = now.isoformat(timespec="seconds")

    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO attachments("
            "profile,kind,ref_id,original_name,stored_name,mime,size,note,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "妈妈", "other", 0, "旧报告.pdf", "old-report.pdf",
                "application/pdf", 100, "", old_at,
            ),
        )
        store._conn.executemany(
            "INSERT INTO attachments("
            "profile,kind,ref_id,original_name,stored_name,mime,size,note,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    "妈妈", "other", 0, f"新附件{index}.pdf",
                    f"new-{index}.pdf", "application/pdf", 100, "",
                    recent_at,
                )
                for index in range(1001)
            ],
        )

    data = client.get(
        "/api/timeline",
        params={"profile": "妈妈", "date": old_day, "type": "attachment"},
    ).json()

    assert [event["title"] for event in data["events"]] == ["旧报告.pdf"]
