"""并发一致性：会诊档案认领、复诊安排与模板套用。"""
import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


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


def test_concurrent_chat_profiles_claim_one_patient(client_and_store):
    _client, store = client_and_store
    api_server._PROFILE_CLAIMS.pop("shared-session", None)

    async def run():
        return await asyncio.gather(
            api_server._resolve_memory_profile("shared-session", "妈妈"),
            api_server._resolve_memory_profile("shared-session", "爸爸"),
        )

    first, second = asyncio.run(run())
    session = store.get_session("shared-session")

    # 两个并发请求必须使用同一档案，不能各用各的上下文写进同一会话；
    # 会话行仍由会诊完成时创建
    assert first == second
    assert first in {"妈妈", "爸爸"}
    assert session is None


def test_concurrent_followup_creates_one_appointment(client_and_store):
    client, store = client_and_store
    visit = client.post("/api/visits", json={
        "profile": "妈妈",
        "diagnosis": "高血压",
        "visit_date": _date(0),
        "next_followup_date": _date(30),
    }).json()["visit"]

    barrier = threading.Barrier(2)
    responses = []

    def call():
        local = TestClient(api_server.app)
        barrier.wait()
        responses.append(local.post(
            f"/api/visits/{visit['id']}/schedule-followup"
        ))

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert [res.status_code for res in responses] == [200, 200]
    appointments = [
        row for row in store.list_appointments(
            "妈妈", None, False, 1000, 0
        )
        if row["title"].startswith("复诊")
    ]
    reminders = [
        row for row in store.list_reminders(True, "妈妈")
        if row.get("appointment_id")
    ]
    assert len(appointments) == 1
    assert len(reminders) == 1


def test_concurrent_template_apply_creates_no_duplicates(
    client_and_store,
):
    client, store = client_and_store
    condition = client.post("/api/conditions", json={
        "profile": "妈妈", "name": "高血压",
    }).json()["condition"]

    barrier = threading.Barrier(2)
    responses = []

    def call():
        local = TestClient(api_server.app)
        barrier.wait()
        responses.append(local.post(
            f"/api/conditions/{condition['id']}/apply-template"
        ))

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert [res.status_code for res in responses] == [200, 200]
    items = store.list_care_items("妈妈", True, False, 1000)
    titles = [row["title"] for row in items]
    assert len(titles) == len(set(titles))
    assert len(titles) == 4


def test_toggle_medication_sends_only_active_field():
    start = SCRIPT.index("async function toggleMedication(item)")
    body = SCRIPT[start:start + 600]

    # 只 PATCH active，避免旧列表快照覆盖并发改名/调量
    assert "JSON.stringify({ active: !item.active })" in body
    assert "name: item.name" not in body


def test_mark_care_done_does_not_read_outside_transaction(
    tmp_path, monkeypatch,
):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        item = store.add_care_item(
            "妈妈", "眼底检查", 30, "", "2026-11-01", "", True,
        )
        monkeypatch.setattr(
            store,
            "get_care_item",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("锁外读取复查计划")
            ),
        )
        done = store.mark_care_item_done(item["id"], "2026-11-02")
    finally:
        store.close()

    assert done["last_done_at"] == "2026-11-02"
    assert done["next_due_at"] == "2026-12-02"
