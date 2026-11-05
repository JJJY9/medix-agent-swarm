"""健康档案 Markdown 导出的离线测试。"""
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


def test_health_dossier_markdown(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    taken_at = now.isoformat(timespec="minutes")

    client.post("/api/profiles", json={
        "name": "妈妈",
        "context": {
            "age": "62", "sex": "女", "allergies": "青霉素",
        },
    })
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 135, "value2": 85,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 140, "value2": 95, "measured_at": taken_at,
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕", "severity": 4,
        "note": "早晨明显", "recorded_at": taken_at,
    })
    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平", "dose": "5 mg",
        "frequency": "每日两次",
    }).json()["medication"]
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": taken_at},
    )
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "复查血压",
        "due_at": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
    })
    store.save_exchange(
        "dossier-session", "妈妈血压高怎么办",
        "风险等级：中危。建议近期复查。",
    )
    store.set_session_profile("dossier-session", "妈妈")

    response = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    )
    text = response.text

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "# 健康档案：妈妈" in text
    assert "62 岁 · 女" in text
    assert "过敏史：青霉素" in text
    assert "血压：≤ 135/85 mmHg" in text
    assert "血压：140/95 mmHg（" in text
    assert "超标" in text
    assert "头晕 4/10（早晨明显）" in text
    assert "氨氯地平 5 mg，每日两次（在用" in text
    assert "依从率 50%" in text
    assert "复查血压（已逾期）" in text
    assert "（中危）" in text
    assert "不构成医疗建议" in text


def test_health_dossier_requires_profile(client_and_store):
    client, _store = client_and_store

    response = client.get("/api/health/dossier.md")

    assert response.status_code == 400
    assert response.json()["detail"] == "请先选择患者档案"
