"""APRI 与 eAG 临床工具测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.clinical_tools import calculate_apri, calculate_eag
from memory.session_store import SessionStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app)
    finally:
        store.close()


def test_apri_calculation_boundaries():
    low = calculate_apri(20, 200)
    assert low["apri"] == 0.25
    assert low["risk"] == "低风险"

    grey = calculate_apri(40, 100)
    assert grey["apri"] == 1.0
    assert grey["risk"] == "不确定"

    high = calculate_apri(80, 100)
    assert high["apri"] == 2.0
    assert high["risk"] == "提示纤维化"

    custom = calculate_apri(60, 100, 30)
    assert custom["apri"] == 2.0

    exact_low = calculate_apri(40, 200)
    assert exact_low["apri"] == 0.5
    assert exact_low["risk"] == "不确定"

    exact_grey = calculate_apri(120, 200)
    assert exact_grey["apri"] == 1.5
    assert exact_grey["risk"] == "不确定"

    rounded_low = calculate_apri(100, 505)
    assert rounded_low["apri"] == 0.5
    assert rounded_low["risk"] == "不确定"

    rounded_grey = calculate_apri(181, 301)
    assert rounded_grey["apri"] == 1.5
    assert rounded_grey["risk"] == "不确定"

    with pytest.raises(ValueError):
        calculate_apri(0, 100)
    with pytest.raises(ValueError):
        calculate_apri(40, 100, 0)


def test_eag_calculation():
    result = calculate_eag(7.0)

    assert result["hba1c"] == 7.0
    assert result["eag_mg_dl"] == 154
    assert result["eag_mmol_l"] == 8.6
    assert result["note"]

    lower = calculate_eag(3)
    assert lower["eag_mg_dl"] == 39
    upper = calculate_eag(20)
    assert upper["eag_mg_dl"] == 527

    with pytest.raises(ValueError):
        calculate_eag(2.5)
    with pytest.raises(ValueError):
        calculate_eag(25)


def test_apri_endpoint(client):
    response = client.post("/api/tools/apri", json={
        "ast": 80, "platelets": 100,
    })

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["apri"] == 2.0
    assert data["risk"] == "提示纤维化"
    assert data["detail"]

    invalid = client.post("/api/tools/apri", json={
        "ast": 80, "platelets": 0,
    })
    assert invalid.status_code == 422
    missing = client.post("/api/tools/apri", json={"ast": 80})
    assert missing.status_code == 422
    bad_uln = client.post("/api/tools/apri", json={
        "ast": 80, "platelets": 100, "ast_uln": 0,
    })
    assert bad_uln.status_code == 422


def test_eag_endpoint(client):
    response = client.post("/api/tools/eag", json={"hba1c": 7.0})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["eag_mg_dl"] == 154
    assert data["eag_mmol_l"] == 8.6

    invalid = client.post("/api/tools/eag", json={"hba1c": 25})
    assert invalid.status_code == 422
    missing = client.post("/api/tools/eag", json={})
    assert missing.status_code == 422
