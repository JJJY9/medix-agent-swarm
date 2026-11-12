"""CSV 状态列解析：用户手写的停用变体不能重新导入为在用。"""
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


@pytest.mark.parametrize(
    "value",
    [
        "停用", "已停用", "停用中", "已停药", "停药", "暂停", "已暂停",
        "停服", "已停服", "0", "false", "FALSE", "no", "No", "否", " 已停用 ",
    ],
)
def test_csv_active_status_inactive_variants(value):
    assert api_server.csv_active_status(value) is False


@pytest.mark.parametrize(
    "value", ["在用", "使用中", "启用", "active", "1", "true", "yes", "是", "on", ""],
)
def test_csv_active_status_active_variants(value):
    assert api_server.csv_active_status(value) is True


def test_csv_active_status_defaults_to_active_for_missing(value=None):
    assert api_server.csv_active_status(value) is True
    assert api_server.csv_active_status(None) is True


def test_medication_import_stopped_status_variants(client_and_store):
    client, _store = client_and_store
    csv_text = "\n".join([
        "药物名称,患者档案,状态",
        "药A,妈妈,已停用",
        "药B,妈妈,停用中",
        "药C,妈妈,已停药",
        "药D,妈妈,暂停",
        "药E,妈妈,在用",
        "药F,妈妈,",
    ]).encode("utf-8")

    imported = client.post(
        "/api/medications/import",
        files={"file": ("medications.csv", csv_text, "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 6

    rows = client.get("/api/medications").json()["medications"]
    active_by_name = {row["name"]: row["active"] for row in rows}
    assert active_by_name == {
        "药A": False,
        "药B": False,
        "药C": False,
        "药D": False,
        "药E": True,
        "药F": True,
    }


def test_allergy_import_stopped_status_variants(client_and_store):
    client, _store = client_and_store
    csv_text = "\n".join([
        "过敏原,患者档案,状态",
        "花粉,妈妈,已停药",
        "尘螨,妈妈,停用中",
        "花生,妈妈,在用",
    ]).encode("utf-8")

    imported = client.post(
        "/api/allergies/import",
        files={"file": ("allergies.csv", csv_text, "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 3

    rows = client.get("/api/allergies").json()["allergies"]
    active_by_name = {row["substance"]: row["active"] for row in rows}
    assert active_by_name == {"花粉": False, "尘螨": False, "花生": True}
