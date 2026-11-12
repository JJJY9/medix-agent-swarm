"""全局健康搜索 API 测试。"""
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


def _seed_family(client):
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 132, "value2": 84,
        "measured_at": f"{_date(0)}T08:00",
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕", "severity": 4,
        "recorded_at": f"{_date(0)}T09:00",
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平", "dose": "5 mg",
        "frequency": "每日一次",
    })
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "ALT", "value": 60, "unit": "U/L",
        "ref_low": 7, "ref_high": 40, "tested_at": f"{_date(0)}T09:00",
    })
    client.post("/api/vaccinations", json={
        "profile": "妈妈", "vaccine_name": "流感疫苗",
        "vaccinated_at": _date(0),
    })
    client.post("/api/appointments", json={
        "profile": "妈妈", "title": "心内科复诊",
        "scheduled_at": f"{_date(3)}T09:00",
    })
    client.post("/api/care-items", json={
        "profile": "妈妈", "title": "糖化血红蛋白",
        "interval_days": 90, "next_due_at": _date(30),
    })
    client.post("/api/visits", json={
        "profile": "妈妈", "visit_date": _date(0),
        "diagnosis": "高血压复诊", "advice": "低盐饮食",
    })
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "青霉素", "reaction": "皮疹",
        "severity": "severe", "kind": "drug", "noted_at": _date(0),
    })
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "原发性高血压", "icd10": "I10",
        "diagnosed_at": _date(-365),
    })
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "取降压药", "due_at": _date(1),
    })


def test_health_search_matches_across_categories(client_and_store):
    client, _store = client_and_store
    _seed_family(client)

    data = client.get(
        "/api/health/search", params={"q": "血压", "profile": "妈妈"}
    ).json()

    types = {item["type"] for item in data["results"]}
    assert {"metric", "condition", "visit"} <= types
    assert data["counts"]["metric"] >= 1
    assert data["total"] >= len(types)
    metric = next(
        item for item in data["results"] if item["type"] == "metric"
    )
    assert metric["label"] == "指标"
    assert metric["metric"] == "bp"
    assert metric["title"].startswith("血压 132/84")
    assert metric["profile"] == "妈妈"
    assert metric["at"]

    medication = client.get(
        "/api/health/search", params={"q": "氨氯地平", "profile": "妈妈"}
    ).json()
    assert [item["type"] for item in medication["results"]] == [
        "medication"
    ]

    vaccine = client.get(
        "/api/health/search", params={"q": "流感", "profile": "妈妈"}
    ).json()
    assert vaccine["results"][0]["type"] == "vaccination"

    abnormal = client.get(
        "/api/health/search", params={"q": "偏高", "profile": "妈妈"}
    ).json()
    assert [item["type"] for item in abnormal["results"]] == ["lab"]
    assert "偏高" in abnormal["results"][0]["detail"]

    allergy = client.get(
        "/api/health/search", params={"q": "皮疹", "profile": "妈妈"}
    ).json()
    assert [item["type"] for item in allergy["results"]] == ["allergy"]

    reminder = client.get(
        "/api/health/search", params={"q": "取降压药", "profile": "妈妈"}
    ).json()
    assert [item["type"] for item in reminder["results"]] == ["reminder"]

    care = client.get(
        "/api/health/search", params={"q": "糖化", "profile": "妈妈"}
    ).json()
    assert [item["type"] for item in care["results"]] == ["care"]

    followup = client.get(
        "/api/health/search", params={"q": "复诊", "profile": "妈妈"}
    ).json()
    assert {"appointment", "visit"} <= {
        item["type"] for item in followup["results"]
    }


def test_health_search_profile_scoping(client_and_store):
    client, _store = client_and_store
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕", "severity": 3,
        "recorded_at": f"{_date(0)}T08:00",
    })
    client.post("/api/symptoms", json={
        "profile": "爸爸", "symptom": "头晕", "severity": 5,
        "recorded_at": f"{_date(0)}T09:00",
    })
    client.post("/api/symptoms", json={
        "symptom": "头晕", "severity": 2,
        "recorded_at": f"{_date(0)}T10:00",
    })
    client.post("/api/lab-results", json={
        "profile": "爸爸", "item": "ALT", "value": 50,
        "tested_at": f"{_date(0)}T09:00",
    })

    mom = client.get(
        "/api/health/search", params={"q": "头晕", "profile": "妈妈"}
    ).json()
    dad = client.get(
        "/api/health/search", params={"q": "头晕", "profile": "爸爸"}
    ).json()
    family = client.get(
        "/api/health/search", params={"q": "头晕"}
    ).json()
    unspecified = client.get(
        "/api/health/search", params={"q": "头晕", "profile": ""}
    ).json()

    assert {item["profile"] for item in mom["results"]} == {"妈妈"}
    assert {item["profile"] for item in dad["results"]} == {"爸爸"}
    assert len(family["results"]) == 3
    assert {item["profile"] for item in unspecified["results"]} == {""}

    lab_search = client.get(
        "/api/health/search", params={"q": "alt", "profile": "爸爸"}
    ).json()
    assert {item["profile"] for item in lab_search["results"]} == {"爸爸"}
    assert [item["type"] for item in lab_search["results"]] == ["lab"]
    unknown = client.get(
        "/api/health/search", params={"q": "头晕", "profile": "不存在"}
    ).json()
    assert unknown["total"] == 0


def test_health_search_validation_and_limits(client_and_store):
    client, _store = client_and_store
    _seed_family(client)
    _seed_family(client)

    blank = client.get("/api/health/search", params={"q": "   "})
    long_query = client.get(
        "/api/health/search", params={"q": "a" * 200}
    )
    limited = client.get(
        "/api/health/search",
        params={"q": "复诊", "limit": 1},
    ).json()
    limit_zero = client.get(
        "/api/health/search", params={"q": "复诊", "limit": 0}
    ).json()
    limit_huge = client.get(
        "/api/health/search", params={"q": "复诊", "limit": 1000}
    ).json()
    limit_negative = client.get(
        "/api/health/search", params={"q": "复诊", "limit": -5}
    ).json()
    percent = client.get(
        "/api/health/search", params={"q": "%"}
    ).json()
    underscore = client.get(
        "/api/health/search", params={"q": "_"}
    ).json()

    assert blank.status_code == 400
    assert long_query.status_code == 200
    assert len(long_query.json()["query"]) == 80
    assert limited["returned"] == 1
    assert len(limited["results"]) == 1
    assert limited["total"] >= 2
    assert limit_zero["returned"] == len(limit_zero["results"])
    assert limit_zero["returned"] <= 50
    assert limit_huge["returned"] <= 200
    assert limit_negative["returned"] == 1
    assert percent["total"] == 0
    assert underscore["total"] == 0


def test_health_search_caps_per_type_and_flags_truncation(
    client_and_store,
):
    client, _store = client_and_store
    for i in range(25):
        client.post("/api/symptoms", json={
            "profile": "妈妈", "symptom": "头晕", "severity": 3,
            "recorded_at": f"{_date(-i)}T08:00",
        })

    data = client.get(
        "/api/health/search",
        params={"q": "头晕", "profile": "妈妈", "limit": 200},
    ).json()

    assert data["counts"]["symptom"] == 25
    assert data["total"] == 25
    assert data["returned"] == 20
    assert len(data["results"]) == 20
    assert data["truncated"] is True
    assert all(item["type"] == "symptom" for item in data["results"])


def test_health_search_matches_status_labels(client_and_store):
    client, _store = client_and_store
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平", "active": False,
    })
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "尘螨", "kind": "other",
        "noted_at": _date(0),
    })
    reminder = client.post("/api/reminders", json={
        "profile": "妈妈", "title": "取药", "due_at": _date(1),
    }).json()["reminder"]
    client.post(f"/api/reminders/{reminder['id']}/complete")

    med = client.get(
        "/api/health/search", params={"q": "停用", "profile": "妈妈"}
    ).json()
    assert [item["type"] for item in med["results"]] == ["medication"]
    allergy = client.get(
        "/api/health/search", params={"q": "其他", "profile": "妈妈"}
    ).json()
    assert [item["type"] for item in allergy["results"]] == ["allergy"]
    done = client.get(
        "/api/health/search", params={"q": "已完成", "profile": "妈妈"}
    ).json()
    assert [item["type"] for item in done["results"]] == ["reminder"]


def test_health_search_sorts_across_types(client_and_store):
    client, _store = client_and_store
    _seed_family(client)

    data = client.get(
        "/api/health/search", params={"q": "复诊", "profile": "妈妈"}
    ).json()

    ats = [item["at"] for item in data["results"]]
    assert ats == sorted(ats, reverse=True)
    assert data["results"][0]["type"] == "appointment"


def test_health_search_sorted_by_time_desc(client_and_store):
    client, _store = client_and_store
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕", "severity": 3,
        "recorded_at": f"{_date(-2)}T08:00",
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕", "severity": 5,
        "recorded_at": f"{_date(0)}T08:00",
    })

    data = client.get(
        "/api/health/search", params={"q": "头晕", "profile": "妈妈"}
    ).json()

    assert [item["at"][:10] for item in data["results"]] == [
        _date(0), _date(-2),
    ]


def test_health_search_attachment_by_linked_record(client_and_store):
    client, store = client_and_store
    visit = client.post("/api/visits", json={
        "profile": "妈妈", "visit_date": _date(0),
        "diagnosis": "复诊", "department": "心内科",
        "doctor": "王医生",
    }).json()["visit"]
    lab = client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "糖化血红蛋白", "value": 7.2,
        "unit": "%", "panel": "内分泌",
        "tested_at": _date(0),
    }).json()["lab_result"]
    store.add_attachment(
        "妈妈", "visit", visit["id"], "扫描件.pdf", "scan.pdf",
        "application/pdf", 100, "",
    )
    store.add_attachment(
        "妈妈", "lab", lab["id"], "化验资料.pdf", "lab-scan.pdf",
        "application/pdf", 100, "",
    )

    by_department = client.get(
        "/api/health/search", params={"q": "心内科", "profile": "妈妈"}
    ).json()
    attachments = [
        row for row in by_department["results"]
        if row["type"] == "attachment"
    ]
    assert [row["title"] for row in attachments] == ["扫描件.pdf"]
    assert "心内科" in attachments[0]["detail"]

    by_lab = client.get(
        "/api/health/search", params={"q": "糖化", "profile": "妈妈"}
    ).json()
    lab_attachments = [
        row for row in by_lab["results"] if row["type"] == "attachment"
    ]
    assert [row["title"] for row in lab_attachments] == ["化验资料.pdf"]
    assert "糖化血红蛋白" in lab_attachments[0]["detail"]


def test_health_search_includes_attachment_metadata(client_and_store):
    client, store = client_and_store
    store.add_attachment(
        "妈妈", "visit", 0, "出院小结.pdf", "a.pdf",
        "application/pdf", 100, "心内科复查",
    )
    store.add_attachment(
        "爸爸", "visit", 0, "爸爸出院资料.pdf", "b.pdf",
        "application/pdf", 100, "",
    )

    data = client.get(
        "/api/health/search", params={"q": "出院", "profile": "妈妈"}
    ).json()

    assert data["counts"]["attachment"] == 1
    item = next(
        row for row in data["results"] if row["type"] == "attachment"
    )
    assert item["title"] == "出院小结.pdf"
    assert item["kind"] == "visit"
    assert item["mime"] == "application/pdf"
    assert "心内科复查" in item["detail"]
    assert all("爸爸" not in row["title"] for row in data["results"])

    by_kind = client.get(
        "/api/health/search", params={"q": "就诊资料", "profile": "妈妈"}
    ).json()
    assert by_kind["counts"]["attachment"] == 1


def test_health_search_attachment_beyond_newest_page(client_and_store):
    client, store = client_and_store
    old_at = (datetime.now() - timedelta(days=5)).isoformat(
        timespec="seconds"
    )
    recent_at = datetime.now().isoformat(timespec="seconds")

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
        "/api/health/search",
        params={"q": "旧报告", "profile": "妈妈"},
    ).json()

    attachments = [
        row for row in data["results"] if row["type"] == "attachment"
    ]
    assert [row["title"] for row in attachments] == ["旧报告.pdf"]


def test_health_search_export_csv(client_and_store):
    client, _store = client_and_store
    _seed_family(client)

    response = client.get(
        "/api/health/search/export.csv",
        params={"q": "头晕", "profile": "妈妈"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(
        response.content.decode("utf-8-sig")
    )))
    assert rows[0] == ["类型", "时间", "患者档案", "标题", "详情"]
    assert any(
        row[2] == "妈妈" and "头晕" in row[3] for row in rows[1:]
    )

    missing = client.get("/api/health/search/export.csv")
    assert missing.status_code == 400


def test_health_search_export_is_not_truncated_at_per_type_limit(
    client_and_store,
):
    client, _store = client_and_store
    for index in range(25):
        response = client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "weight",
            "value1": 60 + index, "measured_at": f"{_date(-index)}T08:00",
            "note": "复查体重",
        })
        assert response.status_code == 200

    search = client.get(
        "/api/health/search",
        params={"q": "复查", "profile": "妈妈", "limit": 200},
    ).json()
    assert search["total"] == 25
    # 页面搜索仍按每类 20 条展示，避免一次渲染过多结果
    assert search["returned"] == 20

    export = client.get(
        "/api/health/search/export.csv",
        params={"q": "复查", "profile": "妈妈"},
    )
    assert export.status_code == 200
    rows = list(csv.reader(io.StringIO(
        export.content.decode("utf-8-sig")
    )))
    assert len(rows) == 26
    assert all("复查体重" in row[4] for row in rows[1:])


def test_health_search_export_can_filter_type(client_and_store):
    client, _store = client_and_store
    _seed_family(client)

    export = client.get(
        "/api/health/search/export.csv",
        params={"q": "血压", "profile": "妈妈", "type": "metric"},
    )
    rows = list(csv.reader(io.StringIO(
        export.content.decode("utf-8-sig")
    )))

    assert export.status_code == 200
    assert len(rows) > 1
    assert all(row[0] == "指标" for row in rows[1:])

    invalid = client.get(
        "/api/health/search/export.csv",
        params={"q": "血压", "type": "unknown"},
    )
    assert invalid.status_code == 400
