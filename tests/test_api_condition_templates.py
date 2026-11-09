"""慢病模板一键生成复查计划与指标目标的测试。"""
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


def _create_condition(client, **overrides):
    payload = {
        "profile": "妈妈",
        "name": "原发性高血压",
        "icd10": "I10",
        "status": "active",
        "severity": "moderate",
        **overrides,
    }
    return client.post("/api/conditions", json=payload).json()["condition"]


def test_hypertension_template_applies_care_plan_and_target(
    client_and_store,
):
    client, _store = client_and_store
    condition = _create_condition(client)
    client.post("/api/care-items", json={
        "profile": "妈妈",
        "title": "血脂",
        "interval_days": 30,
        "next_due_at": _date(10),
    })

    response = client.post(
        f"/api/conditions/{condition['id']}/apply-template"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["template"] == "高血压"
    created_titles = [
        item["title"] for item in data["care_items_created"]
    ]
    assert created_titles == ["肾功能与电解质", "尿常规", "眼底检查"]
    assert data["care_items_skipped"] == ["血脂"]
    assert [row["metric"] for row in data["targets_created"]] == ["bp"]
    assert data["targets_created"][0]["value1"] == 130
    assert data["targets_created"][0]["value2"] == 80

    care_items = client.get(
        "/api/care-items", params={"profile": "妈妈"}
    ).json()["care_items"]
    by_title = {row["title"]: row for row in care_items}
    assert set(by_title) == {
        "血脂", "肾功能与电解质", "尿常规", "眼底检查",
    }
    assert by_title["肾功能与电解质"]["interval_days"] == 180
    assert by_title["眼底检查"]["interval_days"] == 365
    assert by_title["尿常规"]["next_due_at"] == _date(180)
    assert "慢病模板" in by_title["尿常规"]["note"]

    targets = client.get(
        "/api/metric-targets", params={"profile": "妈妈"}
    ).json()["targets"]
    assert [(row["metric"], row["value1"], row["value2"]) for row in targets] == [
        ("bp", 130.0, 80.0),
    ]

    again = client.post(
        f"/api/conditions/{condition['id']}/apply-template"
    ).json()
    assert again["care_items_created"] == []
    assert sorted(again["care_items_skipped"]) == sorted(
        ["血脂", "肾功能与电解质", "尿常规", "眼底检查"]
    )
    assert again["targets_created"] == []
    assert again["targets_skipped"] == ["bp"]

    actions = [
        row["action"] for row in client.get("/api/audit").json()["actions"]
    ]
    assert "condition.template" in actions


def test_diabetes_template_uses_glucose_target(client_and_store):
    client, _store = client_and_store
    condition = _create_condition(
        client, name="2型糖尿病", icd10=""
    )
    assert condition["template_key"] == "diabetes"

    data = client.post(
        f"/api/conditions/{condition['id']}/apply-template"
    ).json()

    created_titles = [
        item["title"] for item in data["care_items_created"]
    ]
    assert created_titles == [
        "糖化血红蛋白", "尿微量白蛋白/肌酐比", "血脂",
        "眼底检查", "足部检查",
    ]
    assert data["targets_created"][0]["metric"] == "glucose"
    assert data["targets_created"][0]["value1"] == 7.0
    assert data["targets_created"][0]["value2"] is None
    hba1c = next(
        item for item in data["care_items_created"]
        if item["title"] == "糖化血红蛋白"
    )
    assert hba1c["interval_days"] == 90


def test_ckd_template_matches_by_icd10(client_and_store):
    client, _store = client_and_store
    condition = _create_condition(
        client, name="肾小球疾病", icd10="N18.3"
    )
    assert condition["template_key"] == "ckd"
    assert condition["template_name"] == "慢性肾脏病"

    data = client.post(
        f"/api/conditions/{condition['id']}/apply-template"
    ).json()

    titles = [item["title"] for item in data["care_items_created"]]
    assert titles == ["血肌酐与 eGFR", "尿蛋白定量", "电解质", "血脂"]
    assert data["targets_created"][0]["metric"] == "bp"


def test_template_skips_existing_metric_target(client_and_store):
    client, _store = client_and_store
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 140, "value2": 90,
    })
    condition = _create_condition(client)

    data = client.post(
        f"/api/conditions/{condition['id']}/apply-template"
    ).json()

    assert data["targets_created"] == []
    assert data["targets_skipped"] == ["bp"]
    targets = client.get(
        "/api/metric-targets", params={"profile": "妈妈"}
    ).json()["targets"]
    assert targets[0]["value1"] == 140.0


def test_dyslipidemia_template_has_no_targets(client_and_store):
    client, _store = client_and_store
    condition = _create_condition(
        client, name="高脂血症", icd10=""
    )
    assert condition["template_key"] == "dyslipidemia"

    data = client.post(
        f"/api/conditions/{condition['id']}/apply-template"
    ).json()

    assert [item["title"] for item in data["care_items_created"]] == [
        "血脂", "肝功能",
    ]
    assert data["targets_created"] == []


def test_condition_without_template_is_rejected(client_and_store):
    client, _store = client_and_store
    condition = _create_condition(
        client, name="偏头痛", icd10="G43"
    )
    assert condition["template_key"] == ""
    assert condition["template_name"] == ""

    rejected = client.post(
        f"/api/conditions/{condition['id']}/apply-template"
    )
    missing = client.post("/api/conditions/99999/apply-template")

    assert rejected.status_code == 400
    assert "模板" in rejected.json()["detail"]
    assert missing.status_code == 404
    assert client.get("/api/care-items").json()["care_items"] == []


def test_condition_list_annotates_templates(client_and_store):
    client, _store = client_and_store
    _create_condition(client, name="原发性高血压", icd10="")
    _create_condition(client, name="偏头痛", icd10="")

    items = client.get(
        "/api/conditions", params={"profile": "妈妈"}
    ).json()["conditions"]
    by_name = {item["name"]: item for item in items}

    assert by_name["原发性高血压"]["template_key"] == "hypertension"
    assert by_name["偏头痛"]["template_key"] == ""
