"""健康数据质量检查 API 测试。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore

LEVELS = {
    "lab_missing_unit": "warning",
    "medication_duplicate": "warning",
    "vaccination_duplicate": "warning",
    "care_duplicate": "warning",
    "reminder_overdue": "info",
    "medication_stock": "warning",
}

SECTIONS = {
    "lab_missing_unit": "lab",
    "medication_duplicate": "medication",
    "vaccination_duplicate": "vaccination",
    "care_duplicate": "care",
    "reminder_overdue": "reminder",
    "medication_stock": "medication",
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


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _quality(client, profile=""):
    params = {"profile": profile} if profile else {}
    response = client.get("/api/health/data-quality", params=params)
    assert response.status_code == 200
    return response.json()


def _by_key(data):
    return {item["key"]: item for item in data["issues"]}


def test_data_quality_reports_common_issues(client_and_store):
    client, _store = client_and_store
    for name, unit in (
        ("ALT", ""),
        ("INR", ""),
        ("PT-INR", ""),
        ("尿白蛋白/肌酐比值", ""),
        ("AST", "U/L"),
    ):
        client.post("/api/lab-results", json={
            "profile": "妈妈", "item": name, "value": 60, "unit": unit,
            "tested_at": f"{_date(0)}T09:00",
        })
    for _ in range(2):
        client.post("/api/medications", json={
            "profile": "妈妈", "name": "氨氯地平",
            "dose": "5 mg", "frequency": "每日一次",
        })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平",
        "dose": "10 mg", "frequency": "每日一次",
    })
    for _ in range(2):
        client.post("/api/medications", json={
            "profile": "妈妈", "name": "阿莫西林",
            "dose": "0.5 g", "frequency": "每日三次",
            "active": False,
        })
    for _ in range(2):
        client.post("/api/vaccinations", json={
            "profile": "妈妈", "vaccine_name": "流感疫苗",
            "dose_number": 1, "vaccinated_at": _date(-30),
        })
    client.post("/api/vaccinations", json={
        "profile": "妈妈", "vaccine_name": "流感疫苗",
        "dose_number": 2, "vaccinated_at": _date(-30),
    })
    for _ in range(2):
        client.post("/api/care-items", json={
            "profile": "妈妈", "title": "眼底检查",
            "interval_days": 365, "next_due_at": _date(30),
        })
    client.post("/api/care-items", json={
        "profile": "妈妈", "title": "眼底检查",
        "interval_days": 365, "next_due_at": _date(30),
        "active": False,
    })
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "取药", "due_at": _date(-2),
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "二甲双胍", "stock": 0,
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "缬沙坦",
        "expiry_date": _date(-5),
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "阿托伐他汀",
        "stock": 0, "expiry_date": _date(-5),
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "华法林",
        "active": False, "stock": 0, "expiry_date": _date(-5),
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "奥美拉唑",
    })

    data = _quality(client, "妈妈")
    issues = _by_key(data)

    assert data["profile"] == "妈妈"
    assert set(issues) == set(LEVELS)
    assert data["total"] == len(data["issues"]) == len(LEVELS)
    # total 是问题种类数，不是记录条数
    assert data["total"] != sum(
        item["count"] for item in data["issues"]
    )

    lab = issues["lab_missing_unit"]
    assert lab["count"] == 2
    assert set(lab["detail"].split("、")) == {
        "ALT", "尿白蛋白/肌酐比值",
    }
    assert issues["medication_duplicate"]["count"] == 1
    assert "×2" in issues["medication_duplicate"]["detail"]
    assert issues["vaccination_duplicate"]["count"] == 1
    assert "第1剂" in issues["vaccination_duplicate"]["detail"]
    assert issues["care_duplicate"]["count"] == 1
    assert "眼底检查" in issues["care_duplicate"]["detail"]
    assert issues["reminder_overdue"]["count"] == 1
    stock = issues["medication_stock"]
    assert stock["count"] == 3
    stock_names = set(stock["detail"].split("、"))
    assert stock_names == {"二甲双胍", "缬沙坦", "阿托伐他汀"}

    for key, item in issues.items():
        assert item["level"] == LEVELS[key]
        assert item["section"] == SECTIONS[key]
        assert item["title"]
        assert item["hint"]

    order = {"warning": 0, "info": 1}
    keys = [(order[item["level"]], -item["count"]) for item in data["issues"]]
    assert keys == sorted(keys)


def test_quality_flags_missing_attachment_files(
    client_and_store, tmp_path, monkeypatch
):
    client, store = client_and_store
    monkeypatch.setattr(
        api_server, "ATTACHMENT_DIR", tmp_path / "attachments"
    )
    store.add_attachment(
        "妈妈", "visit", 0, "出院小结.pdf", "missing.pdf",
        "application/pdf", 10, "",
    )
    data = _quality(client, "妈妈")
    issues = _by_key(data)
    assert issues["attachment_missing"]["count"] == 1
    assert issues["attachment_missing"]["section"] == "attachment"
    assert "出院小结.pdf" in issues["attachment_missing"]["detail"]


def test_quality_flags_attachment_size_mismatch(
    client_and_store, tmp_path, monkeypatch
):
    client, store = client_and_store
    attachment_dir = tmp_path / "attachments"
    attachment_dir.mkdir()
    monkeypatch.setattr(api_server, "ATTACHMENT_DIR", attachment_dir)
    (attachment_dir / "changed.pdf").write_bytes(b"%PDF-1.4 test")
    store.add_attachment(
        "妈妈", "visit", 0, "大小不符.pdf", "changed.pdf",
        "application/pdf", 999, "",
    )

    issues = _by_key(_quality(client, "妈妈"))
    mismatch = issues["attachment_size_mismatch"]
    assert mismatch["count"] == 1
    assert "大小不符.pdf" in mismatch["detail"]
    assert mismatch["section"] == "attachment"
    assert "attachment_missing" not in issues


def test_quality_flags_duplicate_attachment_files(
    client_and_store, tmp_path, monkeypatch
):
    client, store = client_and_store
    attachment_dir = tmp_path / "attachments"
    attachment_dir.mkdir()
    monkeypatch.setattr(api_server, "ATTACHMENT_DIR", attachment_dir)
    rows = (
        ("妈妈", "a.pdf", "出院小结.pdf", b"%PDF-1.4"),
        ("妈妈", "b.pdf", "出院小结.PDF", b"%PDF-1.4"),
        ("妈妈", "c.pdf", "出院小结.pdf", b"%PDF-1.5!"),
        ("爸爸", "d.pdf", "出院小结.pdf", b"%PDF-1.4"),
        ("爸爸", "e.pdf", "出院小结.pdf", b"%PDF-1.4"),
    )
    for profile, stored, name, content in rows:
        (attachment_dir / stored).write_bytes(content)
        store.add_attachment(
            profile, "visit", 0, name, stored,
            "application/pdf", len(content), "",
        )

    issues = _by_key(_quality(client, "妈妈"))
    duplicate = issues["attachment_duplicate"]
    assert duplicate["count"] == 1
    assert "×2" in duplicate["detail"]
    assert "出院小结" in duplicate["detail"]
    assert duplicate["section"] == "attachment"
    assert duplicate["query"].lower() == "出院小结.pdf"
    assert "attachment_missing" not in issues

    family = _by_key(_quality(client))
    assert family["attachment_duplicate"]["count"] == 2


def test_data_quality_ignores_inactive_and_boundary_records(client_and_store):
    client, _store = client_and_store
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "华法林",
        "active": False, "stock": 0, "expiry_date": _date(-10),
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "奥美拉唑",
    })
    done = client.post("/api/reminders", json={
        "profile": "妈妈", "title": "已完成提醒", "due_at": _date(-3),
    }).json()["reminder"]
    client.post(f"/api/reminders/{done['id']}/complete")
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "今天到期", "due_at": _date(0),
    })
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "未来到期", "due_at": _date(3),
    })
    for _ in range(2):
        client.post("/api/care-items", json={
            "profile": "妈妈", "title": "眼底检查",
            "interval_days": 365, "active": False,
        })

    data = _quality(client, "妈妈")

    assert data["total"] == 0
    assert data["issues"] == []


def test_data_quality_grouping_keys_and_counts(client_and_store):
    client, _store = client_and_store
    # 药名大小写/空白归一化后归为一组，剂量不同不并组，档案不同不并组
    for name in ("Amlodipine", "amlodipine "):
        client.post("/api/medications", json={
            "profile": "妈妈", "name": name,
            "dose": "5 mg", "frequency": "每日一次",
        })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "Amlodipine",
        "dose": "10 mg", "frequency": "每日一次",
    })
    client.post("/api/medications", json={
        "profile": "爸爸", "name": "Amlodipine",
        "dose": "5 mg", "frequency": "每日一次",
    })
    for _ in range(2):
        client.post("/api/medications", json={
            "profile": "妈妈", "name": "二甲双胍",
            "dose": "0.5 g", "frequency": "每日两次",
        })
    # 疫苗：剂次或接种日期不同不并组
    common = {
        "profile": "妈妈", "vaccine_name": "乙肝疫苗", "dose_number": 1,
        "vaccinated_at": _date(-90),
    }
    client.post("/api/vaccinations", json=common)
    client.post("/api/vaccinations", json=common)
    client.post("/api/vaccinations", json={
        **common, "dose_number": 2,
    })
    client.post("/api/vaccinations", json={
        **common, "vaccinated_at": _date(-60),
    })
    # 复查计划：同名同档案且都在用的才并组
    client.post("/api/care-items", json={
        "profile": "妈妈", "title": "颈动脉超声", "interval_days": 365,
    })
    client.post("/api/care-items", json={
        "profile": "妈妈", "title": "颈动脉超声", "interval_days": 180,
    })
    client.post("/api/care-items", json={
        "profile": "爸爸", "title": "颈动脉超声", "interval_days": 365,
    })

    data = _quality(client)
    issues = _by_key(data)

    assert issues["medication_duplicate"]["count"] == 2
    assert "Amlodipine ×2" in issues["medication_duplicate"]["detail"]
    assert "二甲双胍 ×2" in issues["medication_duplicate"]["detail"]
    assert issues["vaccination_duplicate"]["count"] == 1
    assert issues["care_duplicate"]["count"] == 1
    assert "颈动脉超声 ×2" in issues["care_duplicate"]["detail"]


def test_data_quality_profile_scope_and_clean_state(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/profiles", json={"name": "爸爸", "context": {}})
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "ALT", "value": 60, "unit": "",
        "tested_at": f"{_date(0)}T09:00",
    })

    dad = _quality(client, "爸爸")
    mom = _quality(client, "妈妈")
    family = _quality(client)

    assert dad["total"] == 0
    assert dad["issues"] == []
    assert mom["total"] == 1
    assert family["total"] == 1


def test_data_quality_lab_unitless_names(client_and_store):
    client, _store = client_and_store
    exempt = (
        "INR", " inr ", "pH", "Ph", "白蛋白/球蛋白比值",
        "国际标准化比值", "HOMA-IR", "PT-INR", "INR（PT）",
        "尿pH", "pH值", "纤维化指数", "评分", "滴度",
    )
    flagged = ("ALT", "肌酐", "尿白蛋白/肌酐比值", "尿蛋白/肌酐比值")
    for name in exempt + flagged:
        client.post("/api/lab-results", json={
            "profile": "妈妈", "item": name, "value": 1,
            "unit": "", "tested_at": f"{_date(0)}T09:00",
        })

    data = _quality(client, "妈妈")
    issues = _by_key(data)

    assert "lab_missing_unit" in issues
    assert issues["lab_missing_unit"]["count"] == len(flagged)
    assert set(issues["lab_missing_unit"]["detail"].split("、")) == set(
        flagged
    )
