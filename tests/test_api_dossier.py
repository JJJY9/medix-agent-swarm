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


def test_health_dossier_includes_attachment_metadata(client_and_store):
    client, store = client_and_store
    store.add_attachment(
        "妈妈", "visit", 0, "出院小结.pdf", "a.pdf",
        "application/pdf", 100, "出院资料",
    )
    store.add_attachment(
        "妈妈", "lab", 0, "化验单.pdf", "b.pdf",
        "application/pdf", 100, "",
    )

    response = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    )
    text = response.text

    assert response.status_code == 200
    assert "## 就医资料" in text
    assert "出院小结.pdf（就诊资料，未关联" in text
    assert "化验单.pdf（化验单，未关联）" in text
    assert "出院资料" in text


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
        "/api/health/dossier.md",
        params={"profile": "妈妈", "days": 7},
    )
    text = response.text

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "## 近 7 天症状" in text
    assert "## 近 7 天会诊" in text
    assert "# 健康档案：妈妈" in text
    assert "62 岁 · 女" in text
    assert "过敏史：青霉素" in text
    assert "连续记录：已连续 1 天（近 30 天记录 1 天）" in text
    assert "血压：≤ 135/85 mmHg" in text
    assert "血压：140/95 mmHg（" in text
    assert "超标" in text
    assert "头晕 4/10（早晨明显）" in text
    assert "氨氯地平 5 mg，每日两次（在用" in text
    assert "依从率 50%" in text
    assert "复查血压（已逾期）" in text
    assert "（中危）" in text
    assert "不构成医疗建议" in text


def test_health_dossier_empty_profile(client_and_store):
    client, _store = client_and_store

    client.post("/api/profiles", json={
        "name": "孩子", "context": {"age": "7"},
    })

    response = client.get(
        "/api/health/dossier.md", params={"profile": "孩子"}
    )
    text = response.text

    assert response.status_code == 200
    assert "# 健康档案：孩子" in text
    assert "连续记录：已连续 0 天（近 30 天记录 0 天）" in text
    assert "暂未设置指标目标" in text
    assert "暂无健康指标记录" in text
    assert "暂无症状记录" in text
    assert "暂无用药记录" in text
    assert "暂无待办提醒" in text
    assert "暂无会诊记录" in text


def test_family_dossier_markdown(client_and_store):
    client, _store = client_and_store

    client.post("/api/profiles", json={
        "name": "妈妈", "context": {"age": "62"},
    })
    client.post("/api/profiles", json={
        "name": "孩子", "context": {"age": "7"},
    })

    response = client.get(
        "/api/health/dossier-family.md", params={"days": 7}
    )
    text = response.text

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert text.count("## 近 7 天症状") == 2
    assert "# 健康档案：妈妈" in text
    assert "# 健康档案：孩子" in text
    assert text.count("# 健康档案：") == 2


def test_family_dossier_requires_profiles(client_and_store):
    client, _store = client_and_store

    response = client.get("/api/health/dossier-family.md")

    assert response.status_code == 400
    assert response.json()["detail"] == "暂无患者档案"


def test_health_dossier_requires_profile(client_and_store):
    client, _store = client_and_store

    response = client.get("/api/health/dossier.md")

    assert response.status_code == 400
    assert response.json()["detail"] == "请先选择患者档案"


def test_dossier_includes_health_insights(client_and_store):
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 190, "value2": 130,
        "measured_at": f"{today}T08:00",
    })
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "取药", "due_at": today,
    })

    export = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    )

    assert export.status_code == 200
    assert "## 健康提示" in export.text
    assert "[危急]" in export.text
    assert "近 30 天出现 1 次危急值" in export.text
    assert "1 次危急值：血压 190/130 mmHg" in export.text
    assert "[需关注]" in export.text
    assert "未来 7 天有 1 条提醒到期" in export.text

    client.post("/api/profiles", json={"name": "爸爸", "context": {}})
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "weight", "value1": 70,
        "measured_at": f"{today}T08:00",
    })
    clear = client.get(
        "/api/health/dossier.md", params={"profile": "爸爸"}
    )
    assert "## 健康提示" in clear.text
    assert "- 近 30 天暂未发现需要特别关注的问题。" in clear.text
    assert "[ok]" not in clear.text


def test_dossier_health_tips_include_medication_safety(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={
        "name": "妈妈", "context": {"allergies": "青霉素"},
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "阿莫西林",
    })

    export = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    )

    assert "[需关注] 在用药物存在 1 条安全提示" in export.text
    assert "青霉素" in export.text
    assert "阿莫西林" in export.text


def test_dossier_health_tips_cover_requested_window(client_and_store):
    client, _store = client_and_store
    old = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 190, "value2": 130,
        "measured_at": f"{old}T08:00",
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "维生素 D",
    })

    wide = client.get(
        "/api/health/dossier.md",
        params={"profile": "妈妈", "days": 365},
    )
    narrow = client.get(
        "/api/health/dossier.md",
        params={"profile": "妈妈", "days": 30},
    )

    assert "近 365 天出现 1 次危急值" in wide.text
    assert "190/130" in wide.text
    assert "- 近 30 天暂未发现需要特别关注的问题。" in narrow.text


def test_health_dossier_marks_low_sleep_direction(client_and_store):
    client, _store = client_and_store
    now = datetime.now().isoformat(timespec="minutes")
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "sleep", "value1": 7,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "sleep", "value1": 5,
        "measured_at": now,
    })

    text = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈", "days": 7}
    ).text

    assert "低于目标" in text
    assert "超标" not in text
