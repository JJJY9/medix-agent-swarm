"""复诊准备单 API 测试。"""
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


def _iso(moment):
    return moment.isoformat(timespec="minutes")


def _seed_mother(store):
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    store.add_metric(
        "妈妈", "bp", 150, 95,
        measured_at=_iso(now.replace(hour=8, minute=0)),
        context="morning",
    )
    store.set_metric_target("妈妈", "bp", 130, 80)
    store.add_metric(
        "妈妈", "weight", 62,
        measured_at=_iso(now.replace(hour=8, minute=5)),
    )
    store.add_lab_result(
        "妈妈", "血钾", 7.0, unit="mmol/L",
        ref_low=3.5, ref_high=5.5,
        tested_at=_iso(now.replace(hour=9, minute=0)),
        panel="电解质",
    )
    store.add_lab_result(
        "妈妈", "ALT", 55, unit="U/L",
        ref_low=7, ref_high=40,
        tested_at=_iso(now.replace(hour=9, minute=5)),
        panel="肝功能",
    )
    store.add_symptom(
        "妈妈", "头痛", 6, recorded_at=_iso(now.replace(hour=10, minute=0))
    )
    store.add_symptom(
        "妈妈", "头痛", 4,
        recorded_at=_iso(now - timedelta(days=2)),
    )
    medication = store.add_medication(
        "妈妈", "氨氯地平", dose="5 mg", frequency="每日一次"
    )
    store.add_medication_log(
        medication["id"], _iso(now.replace(hour=8, minute=30))
    )
    store.add_medication_log(
        medication["id"], _iso(now.replace(hour=20, minute=0)),
        skipped=True, skip_reason="forget",
    )
    store.add_allergy(
        "妈妈", "青霉素", reaction="皮疹", severity="severe", kind="drug"
    )
    store.add_condition("妈妈", "高血压", status="active")
    store.add_reminder(
        "复查肾功能", (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        profile="妈妈",
    )
    store.add_care_item(
        "妈妈", "眼底检查", interval_days=30,
        last_done_at=(now - timedelta(days=40)).strftime("%Y-%m-%d"),
        next_due_at=(now - timedelta(days=10)).strftime("%Y-%m-%d"),
    )
    store.add_visit_record(
        "妈妈", visit_date=(now - timedelta(days=10)).strftime("%Y-%m-%d"),
        department="心内科", doctor="李医生", diagnosis="高血压",
        advice="低盐饮食，规律服药",
        next_followup_date=today,
    )


def test_visit_prep_requires_profile(client_and_store):
    client, _store = client_and_store
    response = client.get("/api/health/visit-prep")
    assert response.status_code == 400
    assert "档案" in response.json()["detail"]


def test_visit_prep_builds_sections_and_questions(client_and_store):
    client, store = client_and_store
    _seed_mother(store)
    response = client.get(
        "/api/health/visit-prep",
        params={"profile": "妈妈", "days": 30},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["profile"] == "妈妈"
    assert data["days"] == 30
    assert data["start"] <= data["end"]

    metrics = {row["metric"]: row for row in data["abnormal_metrics"]}
    assert "bp" in metrics
    assert metrics["bp"]["value"] == "150/95"
    assert metrics["bp"]["over_target"] is True
    assert metrics["bp"]["context"] == "晨起"
    assert "weight" not in metrics

    labs = {row["item"]: row for row in data["abnormal_labs"]}
    assert labs["血钾"]["critical"] is True
    assert labs["ALT"]["abnormal_high"] is True
    assert data["abnormal_labs"][0]["item"] == "血钾"

    assert data["symptoms"][0]["symptom"] == "头痛"
    assert data["symptoms"][0]["count"] == 2
    assert data["symptoms"][0]["max_severity"] == 6

    assert data["medication_summary"]["skipped_count"] == 1
    assert data["medication_summary"]["skip_reasons"][0] == {
        "reason": "忘记", "count": 1,
    }
    assert data["medications"][0]["name"] == "氨氯地平"
    assert data["allergies"][0]["substance"] == "青霉素"
    assert data["allergies"][0]["severity"] == "重度"
    assert data["conditions"][0]["name"] == "高血压"
    assert data["reminders"]["overdue_count"] == 1
    assert data["care_items"][0]["title"] == "眼底检查"
    assert data["care_items"][0]["overdue"] is True

    assert data["last_visit"]["diagnosis"] == "高血压"
    assert data["last_visit"]["department"] == "心内科"
    assert data["last_visit"]["next_followup_date"] == (
        datetime.now().strftime("%Y-%m-%d")
    )

    questions = "\n".join(data["questions"])
    assert "危急值" in questions
    assert "超出目标" in questions
    assert "未服药" in questions
    assert "头痛" in questions
    assert "青霉素" in questions
    assert "逾期" in questions
    assert len(data["questions"]) <= 8

    text = data["text"]
    assert text.startswith("复诊准备单（妈妈")
    assert "上次就诊" in text
    assert "需要告知医生的异常" in text
    assert "建议询问医生" in text
    assert "不构成医疗建议" in text


def test_visit_prep_is_scoped_to_profile(client_and_store):
    client, store = client_and_store
    _seed_mother(store)
    now = datetime.now()
    store.add_metric(
        "爸爸", "bp", 160, 100,
        measured_at=_iso(now.replace(hour=7, minute=0)),
    )
    store.add_lab_result(
        "爸爸", "肌酐", 200, unit="µmol/L",
        ref_low=59, ref_high=104,
        tested_at=_iso(now.replace(hour=7, minute=30)),
    )
    store.add_symptom(
        "爸爸", "胸闷", 8, recorded_at=_iso(now.replace(hour=7, minute=45))
    )

    data = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈"}
    ).json()
    metric_values = {row["value"] for row in data["abnormal_metrics"]}
    assert "160/100" not in metric_values
    assert {row["item"] for row in data["abnormal_labs"]} == {"血钾", "ALT"}
    assert {row["symptom"] for row in data["symptoms"]} == {"头痛"}

    father = client.get(
        "/api/health/visit-prep", params={"profile": "爸爸"}
    ).json()
    assert father["last_visit"] is None
    assert [row["symptom"] for row in father["symptoms"]] == ["胸闷"]


def test_older_critical_metric_not_hidden_by_normal_latest(
    client_and_store,
):
    client, store = client_and_store
    now = datetime.now()
    store.add_metric(
        "妈妈", "spo2", 85,
        measured_at=_iso(now - timedelta(days=2)),
    )
    store.add_metric(
        "妈妈", "spo2", 98,
        measured_at=_iso(now.replace(hour=8, minute=0)),
    )
    data = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈"}
    ).json()
    rows = {
        row["metric"]: row for row in data["abnormal_metrics"]
    }
    assert rows["spo2"]["critical"] is True
    assert rows["spo2"]["value"] == "85"
    assert rows["spo2"]["measured_at"][:10] == (
        now - timedelta(days=2)
    ).strftime("%Y-%m-%d")
    assert "血氧饱和度 85" in data["text"]


def test_active_medication_count_not_truncated(client_and_store):
    client, store = client_and_store
    for index in range(25):
        store.add_medication("妈妈", f"药物{index}")
    data = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈"}
    ).json()
    assert data["medication_summary"]["active_count"] == 25
    assert len(data["medications"]) == 20
    assert "目前用药（25 种）" in data["text"]


def test_visit_prep_clamps_days_and_handles_empty_profile(
    client_and_store,
):
    client, _store = client_and_store
    data = client.get(
        "/api/health/visit-prep",
        params={"profile": "空档案", "days": 9999},
    ).json()
    assert data["days"] == 180
    assert data["abnormal_metrics"] == []
    assert data["abnormal_labs"] == []
    assert data["symptoms"] == []
    assert data["medications"] == []
    assert data["last_visit"] is None
    assert data["questions"] == [
        "近期记录较少，请向医生补充描述症状、用药与检查结果。"
    ]
    assert "不构成医疗建议" in data["text"]

    one_day = client.get(
        "/api/health/visit-prep",
        params={"profile": "空档案", "days": 0},
    ).json()
    assert one_day["days"] == 30
