"""急救信息卡 API 测试。"""
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


def _profile(client, name="妈妈", **context):
    return client.post("/api/profiles", json={
        "name": name, "context": context,
    })


def test_emergency_card_contains_key_information(client_and_store):
    client, _store = client_and_store
    _profile(
        client,
        age="68",
        sex="女",
        blood_type="A+",
        emergency_contacts=[
            "张三 女儿 13800000001", "李四 儿子 13900000002",
        ],
    )
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "青霉素",
        "severity": "severe", "kind": "drug", "reaction": "皮疹",
    })
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "停用过敏原",
        "severity": "mild", "kind": "drug", "active": False,
    })
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "高血压", "status": "active",
    })
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "已治愈的问题", "status": "resolved",
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "华法林", "dose": "3 mg",
        "frequency": "每日一次",
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "维生素 D",
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "停用药物", "active": False,
    })

    response = client.get(
        "/api/health/emergency-card", params={"profile": "妈妈"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    text = response.text
    assert "急救信息卡" in text
    assert "妈妈" in text
    assert "A+" in text
    assert "68 岁" in text
    assert "女" in text
    assert "张三 女儿 13800000001" in text
    assert "李四 儿子 13900000002" in text
    assert "青霉素" in text
    assert "重度" in text
    assert "皮疹" in text
    assert "停用过敏原" not in text
    assert "华法林 3 mg 每日一次" in text
    assert "抗凝/抗血小板" in text
    assert "维生素 D" in text
    assert "高血压" in text
    assert "已治愈的问题" not in text
    assert "停用药物" not in text
    assert "拨打 120" in text
    assert "浏览器菜单打印" in text

    # 两位联系人按「；」保存与拆分，不能被合并成一条
    profiles = client.get("/api/profiles").json()["profiles"]
    mom_context = next(
        item["context"] for item in profiles
        if item["name"] == "妈妈"
    )
    assert mom_context["emergency_contacts"] == (
        "张三 女儿 13800000001；李四 儿子 13900000002"
    )


def test_emergency_card_requires_profile(client_and_store):
    client, _store = client_and_store
    response = client.get("/api/health/emergency-card")
    assert response.status_code == 400
    assert "档案" in response.json()["detail"]


def test_emergency_card_handles_missing_optional_fields(client_and_store):
    client, _store = client_and_store
    assert _profile(client, age="70").status_code == 200
    response = client.get(
        "/api/health/emergency-card", params={"profile": "妈妈"}
    )
    assert response.status_code == 200
    text = response.text
    assert "血型" in text
    assert "不详" in text
    assert "未登记紧急联系人" in text
    assert "未登记活动过敏史" in text


def test_emergency_card_warns_for_unknown_profile(client_and_store):
    client, _store = client_and_store
    response = client.get(
        "/api/health/emergency-card", params={"profile": "不存在的档案"}
    )
    assert response.status_code == 200
    assert "未找到该档案的保存信息" in response.text


def test_emergency_card_includes_context_free_text(client_and_store):
    client, _store = client_and_store
    assert _profile(
        client,
        allergies="青霉素过敏",
        medications="华法林 3mg 每日一次",
        history="高血压",
    ).status_code == 200
    text = client.get(
        "/api/health/emergency-card", params={"profile": "妈妈"}
    ).text
    assert "自述：青霉素过敏" in text
    assert "自述：华法林 3mg 每日一次" in text
    assert "含抗凝/抗血小板记录" in text
    assert "自述：高血压" in text
    assert "未登记活动过敏史" not in text
    assert "未登记在用药物" not in text


def test_emergency_card_requires_token_when_enabled(
    client_and_store, monkeypatch,
):
    client, _store = client_and_store
    assert _profile(client, age="70").status_code == 200
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "secret-token")
    unauthorized = client.get(
        "/api/health/emergency-card", params={"profile": "妈妈"}
    )
    assert unauthorized.status_code == 401
    authorized = client.get(
        "/api/health/emergency-card", params={"profile": "妈妈"},
        headers={"X-Medix-Token": "secret-token"},
    )
    assert authorized.status_code == 200


def test_emergency_card_escapes_html(client_and_store):
    client, _store = client_and_store
    assert _profile(
        client, age="70", blood_type="<b>A+</b>",
    ).status_code == 200
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "<script>alert(1)</script>",
        "status": "active",
    })
    response = client.get(
        "/api/health/emergency-card", params={"profile": "妈妈"}
    )
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "&lt;b&gt;A+&lt;/b&gt;" in response.text
    assert "<b>A+</b>" not in response.text
