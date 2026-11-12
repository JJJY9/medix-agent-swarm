"""健康档案导出的化验窗口与条数上限。"""
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


def _at(days_ago):
    return (datetime.now() - timedelta(days=days_ago)).isoformat(
        timespec="minutes"
    )


def test_dossier_lab_section_respects_days_window(client_and_store):
    client, store = client_and_store
    store.add_lab_result(
        "妈妈", "ALT", 55, "U/L", 7, 40, tested_at=_at(5),
    )
    store.add_lab_result(
        "妈妈", "AST", 25, "U/L", 13, 35, tested_at=_at(100),
    )

    text = client.get(
        "/api/health/dossier.md",
        params={"profile": "妈妈", "days": 30},
    ).text

    assert "ALT" in text
    assert "AST" not in text


def test_dossier_lab_section_caps_rendered_rows(client_and_store):
    client, store = client_and_store
    payload = {
        "lab_results": [
            {
                "profile": "妈妈",
                "item": f"项目{index}",
                "value": 10 + index,
                "unit": "U/L",
                "tested_at": _at(2),
            }
            for index in range(305)
        ]
    }
    imported = store.import_data(payload)
    assert imported["lab_results"] == 305

    text = client.get(
        "/api/health/dossier.md",
        params={"profile": "妈妈", "days": 30},
    ).text
    rendered = [
        line for line in text.splitlines() if line.startswith("- 项目")
    ]

    assert len(rendered) == 300
    assert "仅显示近 30 天内最近 300 条" in text
