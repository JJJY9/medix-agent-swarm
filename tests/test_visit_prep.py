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
    assert data["medication_summary"]["taken_count"] == 1
    assert data["medication_summary"]["skip_reasons"][0] == {
        "reason": "忘记", "count": 1,
    }
    assert data["medications"][0]["name"] == "氨氯地平"
    assert data["allergies"][0]["substance"] == "青霉素"
    assert data["allergies"][0]["severity"] == "重度"
    assert data["conditions"][0]["name"] == "高血压"
    assert data["reminders"]["overdue_count"] == 1
    assert data["reminders"]["pending_count"] == 1
    assert data["reminders"]["items"][0]["title"] == "复查肾功能"
    assert data["reminders"]["items"][0]["overdue"] is True
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
    assert "上次就诊建议" in questions
    assert "复查计划" in questions
    assert len(data["questions"]) == 7

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
    old_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    assert rows["spo2"]["measured_at"][:10] == old_date
    assert "血氧饱和度 85" in data["text"]
    assert (
        f"血氧饱和度 {old_date} 为 85"
        in "\n".join(data["questions"])
    )


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


def test_visit_prep_isolation_covers_all_collections(client_and_store):
    client, store = client_and_store
    _seed_mother(store)
    now = datetime.now()
    store.add_metric(
        "爸爸", "weight", 60,
        measured_at=_iso(now.replace(hour=7, minute=0)),
    )
    store.set_metric_target("爸爸", "weight", 55)
    father_med = store.add_medication(
        "爸爸", "二甲双胍", dose="0.5 g", frequency="每日两次"
    )
    store.add_medication_log(
        father_med["id"], _iso(now.replace(hour=8, minute=0)),
        skipped=True, skip_reason="cost",
    )
    store.add_allergy("爸爸", "磺胺类", severity="moderate", kind="drug")
    store.add_condition("爸爸", "糖尿病", status="active")
    store.add_reminder(
        "复查血糖", (now + timedelta(days=3)).strftime("%Y-%m-%d"),
        profile="爸爸",
    )
    store.add_care_item(
        "爸爸", "颈动脉超声", interval_days=365,
        last_done_at=(now - timedelta(days=400)).strftime("%Y-%m-%d"),
        next_due_at=(now - timedelta(days=5)).strftime("%Y-%m-%d"),
    )
    # 已停用/已痊愈的记录不应出现
    store.add_allergy(
        "妈妈", "海鲜", severity="mild", kind="food", active=False
    )
    store.add_condition("妈妈", "旧问题", status="resolved")
    old_med = store.add_medication("妈妈", "旧降压药", active=False)
    store.add_medication_log(
        old_med["id"], _iso(now.replace(hour=9, minute=0)),
        skipped=True, skip_reason="side_effect",
    )

    mother = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈"}
    ).json()
    assert [row["name"] for row in mother["medications"]] == ["氨氯地平"]
    assert mother["medication_summary"]["skipped_count"] == 1
    assert mother["medication_summary"]["skip_reasons"][0]["reason"] == "忘记"
    assert "旧降压药" not in mother["text"]
    assert "旧降压药" not in "\n".join(mother["questions"])
    assert [row["substance"] for row in mother["allergies"]] == ["青霉素"]
    assert [row["name"] for row in mother["conditions"]] == ["高血压"]
    assert [row["title"] for row in mother["reminders"]["items"]] == [
        "复查肾功能"
    ]
    assert [row["title"] for row in mother["care_items"]] == ["眼底检查"]
    # 爸爸的体重目标 55 不能套用到妈妈的 62 kg 上
    assert "weight" not in {
        row["metric"] for row in mother["abnormal_metrics"]
    }

    father = client.get(
        "/api/health/visit-prep", params={"profile": "爸爸"}
    ).json()
    assert [row["name"] for row in father["medications"]] == ["二甲双胍"]
    assert father["medication_summary"]["skip_reasons"][0]["reason"] == "费用"
    assert [row["substance"] for row in father["allergies"]] == ["磺胺类"]
    assert [row["name"] for row in father["conditions"]] == ["糖尿病"]
    assert [row["title"] for row in father["reminders"]["items"]] == [
        "复查血糖"
    ]
    assert [row["title"] for row in father["care_items"]] == ["颈动脉超声"]
    assert father["abnormal_metrics"][0]["metric"] == "weight"


def test_visit_prep_respects_days_window(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    store.add_metric(
        "时间窗", "bp", 150, 95,
        measured_at=_iso(now - timedelta(days=40)),
    )
    store.set_metric_target("时间窗", "bp", 130, 80)
    store.add_lab_result(
        "时间窗", "ALT", 80, unit="U/L",
        ref_low=7, ref_high=40,
        tested_at=_iso(now - timedelta(days=40)),
    )
    store.add_symptom(
        "时间窗", "头痛", 8,
        recorded_at=_iso(now - timedelta(days=40)),
    )
    thirty = client.get(
        "/api/health/visit-prep",
        params={"profile": "时间窗", "days": 30},
    ).json()
    assert thirty["start"] == (
        now - timedelta(days=29)
    ).strftime("%Y-%m-%d")
    assert thirty["end"] == now.strftime("%Y-%m-%d")
    assert thirty["abnormal_metrics"] == []
    assert thirty["abnormal_labs"] == []
    assert thirty["symptoms"] == []

    ninety = client.get(
        "/api/health/visit-prep",
        params={"profile": "时间窗", "days": 90},
    ).json()
    assert ninety["abnormal_metrics"][0]["value"] == "150/95"
    assert ninety["abnormal_labs"][0]["item"] == "ALT"
    assert ninety["symptoms"][0]["symptom"] == "头痛"


def test_visit_prep_latest_metric_lab_and_visit_order(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    store.add_metric(
        "排序", "bp", 150, 95,
        measured_at=_iso(now - timedelta(days=10)),
    )
    store.set_metric_target("排序", "bp", 130, 80)
    store.add_metric(
        "排序", "bp", 120, 80,
        measured_at=_iso(now - timedelta(days=1)),
    )
    store.add_lab_result(
        "排序", "血红蛋白", 130, unit="g/L",
        ref_low=115, ref_high=150,
        tested_at=_iso(now - timedelta(days=1)),
    )
    store.add_lab_result(
        "排序", "血钠", 130, unit="mmol/L",
        ref_low=135, ref_high=145,
        tested_at=_iso(now - timedelta(days=1)),
    )
    store.add_lab_result(
        "排序", "ALT", 100, unit="U/L",
        ref_low=7, ref_high=40,
        tested_at=_iso(now - timedelta(days=10)),
    )
    store.add_lab_result(
        "排序", "ALT", 50, unit="U/L",
        ref_low=7, ref_high=40,
        tested_at=_iso(now - timedelta(days=1)),
    )
    store.add_lab_result(
        "排序", "血钾", 7.0, unit="mmol/L",
        ref_low=3.5, ref_high=5.5,
        tested_at=_iso(now - timedelta(days=20)),
    )
    store.add_lab_result(
        "排序", "血钾", 4.0, unit="mmol/L",
        ref_low=3.5, ref_high=5.5,
        tested_at=_iso(now - timedelta(days=1)),
    )
    store.add_visit_record(
        "排序", visit_date=(now - timedelta(days=20)).strftime("%Y-%m-%d"),
        department="旧科室", diagnosis="旧诊断",
    )
    store.add_visit_record(
        "排序", visit_date=(now - timedelta(days=5)).strftime("%Y-%m-%d"),
        department="新科室", diagnosis="新诊断",
    )

    data = client.get(
        "/api/health/visit-prep", params={"profile": "排序"}
    ).json()
    # 最新一次血压正常，不应把 10 天前的超标记录当作当前异常
    assert "bp" not in {
        row["metric"] for row in data["abnormal_metrics"]
    }
    labs = {row["item"]: row for row in data["abnormal_labs"]}
    assert "血红蛋白" not in labs
    assert labs["血钠"]["abnormal_low"] is True
    assert labs["血钠"]["abnormal_high"] is False
    alt_values = [
        row["value"] for row in data["abnormal_labs"]
        if row["item"] == "ALT"
    ]
    assert alt_values == ["50", "100"]
    assert all(
        question.count("ALT") <= 1 for question in data["questions"]
    )
    assert labs["血钾"]["critical"] is True
    assert labs["血钾"]["value"] == "7"
    assert data["abnormal_labs"][0]["item"] == "血钾"
    assert "血钠 130 mmol/L" in data["text"]
    assert "偏低" in data["text"]
    assert "血红蛋白" not in data["text"]
    assert data["last_visit"]["department"] == "新科室"
    assert data["last_visit"]["diagnosis"] == "新诊断"


def test_visit_prep_reminder_and_care_boundaries(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    store.add_reminder(
        "今天复查", now.strftime("%Y-%m-%d"), profile="边界"
    )
    store.add_reminder(
        "三天后复查",
        (now + timedelta(days=3)).strftime("%Y-%m-%d"),
        profile="边界",
    )
    store.add_care_item(
        "边界", "三天内复查", interval_days=30,
        last_done_at=(now - timedelta(days=27)).strftime("%Y-%m-%d"),
        next_due_at=(now + timedelta(days=3)).strftime("%Y-%m-%d"),
    )
    store.add_care_item(
        "边界", "十天后复查", interval_days=30,
        last_done_at=(now - timedelta(days=20)).strftime("%Y-%m-%d"),
        next_due_at=(now + timedelta(days=10)).strftime("%Y-%m-%d"),
    )
    data = client.get(
        "/api/health/visit-prep", params={"profile": "边界"}
    ).json()
    assert data["reminders"]["pending_count"] == 2
    assert data["reminders"]["overdue_count"] == 0
    assert all(
        item["overdue"] is False
        for item in data["reminders"]["items"]
    )
    assert [item["title"] for item in data["care_items"]] == ["三天内复查"]
    assert data["care_items"][0]["overdue"] is False
    assert "十天后复查" not in data["text"]
    assert "三天内复查" in data["text"]
    assert "临近" in data["text"]


def test_visit_prep_fallback_when_records_are_normal(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    store.add_metric(
        "正常记录", "weight", 60,
        measured_at=_iso(now),
    )
    data = client.get(
        "/api/health/visit-prep", params={"profile": "正常记录"}
    ).json()
    assert data["abnormal_metrics"] == []
    assert data["questions"] == [
        "近期记录较少，请向医生补充描述症状、用药与检查结果。"
    ]


def test_visit_prep_direction_and_question_wording(
    client_and_store, monkeypatch
):
    client, store = client_and_store
    # 让“明天”的化验记录在存储层被视为可接受的时间，以验证接口窗口过滤
    from memory import session_store as store_module

    monkeypatch.setattr(
        store_module, "FUTURE_TIME_TOLERANCE", timedelta(days=2)
    )
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    store.add_metric(
        "方向", "spo2", 92,
        measured_at=_iso(now.replace(hour=8, minute=0)),
    )
    store.set_metric_target("方向", "spo2", 95)
    store.add_symptom(
        "方向", "咳嗽", 3,
        recorded_at=_iso(now.replace(hour=9, minute=0)),
    )
    store.add_symptom(
        "方向", "乏力", 0,
        recorded_at=_iso(now.replace(hour=10, minute=0)),
    )
    store.add_allergy(
        "方向", "花生", severity="moderate", kind="food"
    )
    medication = store.add_medication(
        "方向", "氨氯地平", dose="5 mg", frequency="每日一次"
    )
    store.add_reminder(
        "用药：氨氯地平",
        (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        profile="方向", medication_id=medication["id"],
    )
    store.add_lab_result(
        "方向", "ALT", 100, unit="U/L",
        ref_low=7, ref_high=40,
        tested_at=tomorrow.replace(hour=8, minute=0).isoformat(
            timespec="minutes"
        ),
    )

    data = client.get(
        "/api/health/visit-prep", params={"profile": "方向"}
    ).json()
    spo2 = next(
        row for row in data["abnormal_metrics"]
        if row["metric"] == "spo2"
    )
    assert spo2["over_target"] is True
    assert spo2["target_direction"] == "low"
    assert spo2["critical"] is False
    assert "低于目标" in data["text"]
    assert [row["symptom"] for row in data["symptoms"]] == ["咳嗽"]
    assert data["abnormal_labs"] == []
    questions = "\n".join(data["questions"])
    assert "低于目标" in questions
    assert "饮食上需要避开吗" in questions
    assert "用药提醒已逾期" in questions
    assert "出现咳嗽" in questions
    assert "反复出现咳嗽" not in questions
    assert "乏力" not in data["text"]
    assert "ALT" not in data["text"]


def test_visit_prep_lists_attachments(client_and_store):
    client, store = client_and_store
    store.add_attachment(
        "妈妈", "visit", 0, "出院小结.pdf", "visit.pdf",
        "application/pdf", 100, "",
    )
    store.add_attachment(
        "妈妈", "lab", 123, "化验单.pdf", "lab.pdf",
        "application/pdf", 100, "",
    )
    store.add_attachment(
        "爸爸", "visit", 0, "爸爸资料.pdf", "father.pdf",
        "application/pdf", 100, "",
    )
    data = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈"}
    ).json()
    assert data["attachment_count"] == 2
    assert [row["name"] for row in data["attachments"]] == [
        "化验单.pdf", "出院小结.pdf",
    ]
    assert data["attachments"][0]["linked"] is True
    assert data["attachments"][1]["linked"] is False
    assert "携带资料（2 份）" in data["text"]
    assert "化验单.pdf（化验单，已关联）" in data["text"]
    assert "出院小结.pdf（就诊资料）" in data["text"]
    assert "爸爸资料.pdf" not in data["text"]

    for index in range(11):
        store.add_attachment(
            "妈妈", "other", 0, f"资料{index}.pdf", f"other{index}.pdf",
            "application/pdf", 100, "",
        )
    many = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈"}
    ).json()
    assert many["attachment_count"] == 13
    assert len(many["attachments"]) == 10
    assert "另有 3 份未列出" in many["text"]


def test_visit_prep_attachment_ref_labels(client_and_store):
    client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    visit = client.post("/api/visits", json={
        "profile": "妈妈", "visit_date": today,
        "diagnosis": "复诊", "department": "心内科",
    }).json()["visit"]
    lab = client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "ALT", "value": 30,
        "unit": "U/L", "tested_at": today,
    }).json()["lab_result"]
    store.add_attachment(
        "妈妈", "visit", visit["id"], "出院小结.pdf", "visit.pdf",
        "application/pdf", 100, "",
    )
    store.add_attachment(
        "妈妈", "lab", lab["id"], "化验单.pdf", "lab.pdf",
        "application/pdf", 100, "",
    )

    data = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈"}
    ).json()
    rows = {row["name"]: row for row in data["attachments"]}
    assert "心内科" in rows["出院小结.pdf"]["ref_label"]
    assert "复诊" in rows["出院小结.pdf"]["ref_label"]
    assert "ALT 30 U/L" in rows["化验单.pdf"]["ref_label"]
    assert "心内科" in data["text"]
    assert "ALT 30 U/L" in data["text"]


def test_visit_prep_since_last_visit(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    visit_date = (now - timedelta(days=40)).strftime("%Y-%m-%d")
    store.add_visit_record(
        "妈妈", visit_date=visit_date, department="心内科",
        diagnosis="高血压复诊",
    )
    data = client.get(
        "/api/health/visit-prep",
        params={"profile": "妈妈", "since_last_visit": True},
    ).json()
    assert data["since_last_visit"] is True
    assert data["start"] == visit_date
    assert data["days"] == 41

    fallback = client.get(
        "/api/health/visit-prep",
        params={"profile": "无就诊", "since_last_visit": True},
    ).json()
    assert fallback["since_last_visit"] is False
    assert fallback["days"] == 30


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


def test_visit_prep_attachment_count_beyond_list_page(client_and_store):
    client, store = client_and_store
    now = datetime.now().isoformat(timespec="seconds")
    with store._lock, store._conn:
        store._conn.executemany(
            "INSERT INTO attachments("
            "profile,kind,ref_id,original_name,stored_name,mime,size,"
            "note,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    "妈妈", "other", 0, f"资料{index}.pdf",
                    f"prep-{index}.pdf", "application/pdf", 10, "",
                    now,
                )
                for index in range(1001)
            ],
        )

    data = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈"}
    ).json()

    assert data["attachment_count"] == 1001
    assert "携带资料（1001 份）" in data["text"]
    assert "另有 991 份未列出" in data["text"]
