"""第三十三轮审查：记忆作用域/导入身份/附件上限/鉴权代理/来源/会话流。"""
import asyncio
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path):
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        yield session_store
    finally:
        session_store.close()


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient(api_server.app), session_store
    finally:
        session_store.close()


def _load_script(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- 档案墓碑
def test_profile_rebinding_blocked_with_retained_data(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_metric("妈妈", "weight", 60)
    store.delete_profile("妈妈")

    # 删除档案按约定保留健康数据，但墓碑必须仍然阻止重新绑定
    assert store.profile_rebinding_blocked("妈妈") is True

    store.save_profile("妈妈", {"sex": "女"})
    assert store.profile_rebinding_blocked("妈妈") is False


def test_link_profile_not_rebound_with_retained_data(client_and_store):
    import api_server

    _client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.add_metric("妈妈", "weight", 60)
    store.ensure_session("r33-session")
    store.delete_profile("妈妈")

    asyncio.run(api_server._link_patient_profile("r33-session", "妈妈"))

    session = store.get_session("r33-session")
    assert session["profile"] == ""


def test_add_session_summary_rejects_empty_scope():
    from memory.long_term import LongTermMemory

    memory = LongTermMemory.__new__(LongTermMemory)
    memory.enabled = True
    memory._api_key = ""
    memory.last_error = ""
    memory.ensure_available = lambda force=False: True

    class FakeMem0:
        def __init__(self):
            self.calls = []

        def add(self, **kwargs):
            self.calls.append(kwargs)
            return {"results": [{"id": "m1"}]}

    memory.mem0 = FakeMem0()

    # 空作用域（档案已删除等）不能落回共享 medix_local
    assert memory.add_session_summary("s1", "问题", "回答", user_id="") is None
    assert memory.mem0.calls == []


def test_memory_scope_cleanup_pending_matches_scope(store):
    from core.session_context import (
        profile_memory_scope,
        session_memory_scope,
    )

    store.queue_memory_cleanup([("妈妈", ""), ("", "sess-r33")])

    assert store.memory_scope_cleanup_pending(profile_memory_scope("妈妈"))
    assert store.memory_scope_cleanup_pending(
        session_memory_scope("sess-r33")
    )
    assert not store.memory_scope_cleanup_pending(
        profile_memory_scope("爸爸")
    )

    store.complete_memory_cleanup([("妈妈", ""), ("", "sess-r33")])
    assert not store.memory_scope_cleanup_pending(
        profile_memory_scope("妈妈")
    )


def test_cleanup_queue_keeps_full_session_id(store):
    session_id = "s" * 72
    store.queue_memory_cleanup([("", session_id)])
    rows = store.list_memory_cleanup()
    assert rows and rows[0]["session_id"] == session_id

    store.complete_memory_cleanup([("", session_id)])
    assert store.list_memory_cleanup() == []


def test_search_similar_cases_skips_pending_cleanup(monkeypatch):
    module = _load_script(
        ".claude/skills/search-similar-cases/script/search.py",
        "search_similar_cases_round33",
    )
    import memory as memory_module
    from core.session_context import use_memory_scope

    class FakeStore:
        def memory_scope_cleanup_pending(self, scope):
            return True

    monkeypatch.setattr(
        memory_module, "get_session_store", lambda: FakeStore()
    )

    def _boom():
        raise AssertionError("清理未完成时不应构造 Mem0 客户端")

    monkeypatch.setattr(
        "memory.long_term.LongTermMemory", _boom, raising=False
    )

    with use_memory_scope("medix_profile_deadbeef"):
        result = asyncio.run(module.search_similar_cases("高血压"))

    assert result["total_found"] == 0
    assert "清理尚未完成" in result["answer"]


# ---------------------------------------------------------------- 导入身份
def test_import_restores_deleted_twin_after_profile_move(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    first = store.add_medication("妈妈", "二甲双胍", dose="0.5g")
    second = store.add_medication("爸爸", "二甲双胍", dose="0.5g")
    same_second = "2026-01-01T08:00:00"
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ? WHERE id IN (?, ?)",
            (same_second, first["id"], second["id"]),
        )
    backup = store.export_data()

    # 把 A 改挂到爸爸档案，再删除同秒创建的 B，然后恢复改动前的备份
    assert store.update_medication(first["id"], profile="爸爸")
    assert store.delete_medication(second["id"])

    result = store.import_data(backup)

    # 改挂行不能被兜底配对重复认领：被删的 B 必须补回
    assert result["medications"] == 1
    assert len(store.list_medications(limit=100)) == 2


# ---------------------------------------------------------------- 导出字节
def test_export_rejects_payload_over_byte_cap(store, monkeypatch):
    import memory.session_store as session_store_module

    store.save_exchange("big", "问题", "答" * 4000)
    monkeypatch.setattr(
        session_store_module, "MAX_BACKUP_FILE_BYTES", 2048
    )

    with pytest.raises(ValueError, match="单个文件上限"):
        store.export_data()


# ---------------------------------------------------------------- 就诊提醒同步
def test_update_appointment_syncs_generated_reminder(store):
    appointment = store.add_appointment(
        "妈妈", "牙科复诊",
        department="口腔科", doctor="张医生", location="一号楼",
        scheduled_at="2026-12-01T09:00",
    )
    reminder, _created = store.ensure_reminder(
        "就诊：牙科复诊", "2026-11-30",
        "2026-12-01 09:00 口腔科 张医生 一号楼", "", "妈妈",
        0, 0, 0, appointment["id"],
    )

    assert store.update_appointment(
        appointment["id"], "妈妈", "口腔检查",
        department="口腔颌面外科", doctor="李医生", location="一号楼",
        scheduled_at="2026-12-05T15:00", status="upcoming",
    )

    updated = store.get_reminder(reminder["id"])
    assert updated["title"] == "就诊：口腔检查"
    assert "2026-12-05 15:00" in updated["note"]
    assert "口腔颌面外科" in updated["note"]
    assert updated["due_at"] == "2026-12-04"

    # 用户手改过的备注不能被后续改期覆盖
    store.update_reminder(reminder["id"], note="手改备注")
    assert store.update_appointment(
        appointment["id"], "妈妈", "复诊二号",
        department="口腔颌面外科", doctor="李医生", location="一号楼",
        scheduled_at="2026-12-06T15:00", status="upcoming",
    )
    updated = store.get_reminder(reminder["id"])
    assert updated["title"] == "就诊：复诊二号"
    assert updated["note"] == "手改备注"


# ---------------------------------------------------------------- 科普提问
def test_patient_complaint_text_filters_definition_questions():
    from core.triage import patient_complaint_text

    assert patient_complaint_text("什么是咯血？") == ""
    assert patient_complaint_text("咯血是什么？") == ""
    assert patient_complaint_text("高血压有哪些症状") == ""
    assert "咯血" in patient_complaint_text("我父亲最近咯血是什么原因？")
    assert "胸痛" in patient_complaint_text("突发胸痛，出冷汗")


def test_validator_ignores_definition_questions():
    from constraints.validator import ConstraintValidator

    validator = ConstraintValidator()
    definition = validator.validate_output(
        "diagnostic_agent",
        "咯血是指喉以下呼吸道出血，常见于支气管扩张等疾病。",
        user_input="什么是咯血？",
    )
    assert definition["valid"] is True

    complaint = validator.validate_output(
        "diagnostic_agent",
        "咯血是指喉以下呼吸道出血，常见于支气管扩张等疾病。",
        user_input="我父亲突发咯血，出冷汗",
    )
    assert complaint["valid"] is False


# ---------------------------------------------------------------- 鉴权与同源
def test_loopback_with_forwarded_headers_is_not_trusted(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    assert api_server._is_loopback_client(FakeRequest({})) is True
    assert api_server._is_loopback_client(
        FakeRequest({"x-forwarded-for": "203.0.113.9"})
    ) is False
    assert api_server._is_loopback_client(
        FakeRequest({"forwarded": "for=203.0.113.9"})
    ) is False

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    assert api_server._is_loopback_client(
        FakeRequest({"x-forwarded-for": "203.0.113.9"})
    ) is True


def test_same_origin_ignores_scheme_behind_tls_proxy(client_and_store):
    client, _store = client_and_store

    allowed = client.post(
        "/api/sessions", json={},
        headers={
            "Origin": "https://testserver",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert allowed.status_code != 403

    rejected = client.post(
        "/api/sessions", json={},
        headers={"Origin": "https://evil.example"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "csrf_rejected"


# ---------------------------------------------------------------- 附件目录
def test_attachment_dir_owner_rename_takeover(tmp_path):
    from core.storage_paths import attachment_dir_for_db

    legacy = tmp_path / "attachments"
    legacy.mkdir()
    (legacy / "a.pdf").write_bytes(b"pdf")
    (tmp_path / ".medix-attachments-owner").write_text(
        "family.db", encoding="utf-8"
    )
    family = tmp_path / "family.db"
    family.write_bytes(b"db")
    home = tmp_path / "home.db"
    family.rename(home)

    resolved = attachment_dir_for_db(home)

    # 数据库改名后旧标记过期：接管既有附件目录，而不是显示全部缺失
    assert resolved == legacy
    assert (legacy / "a.pdf").exists()


def test_attachment_dir_case_only_session_db(tmp_path):
    from core.storage_paths import attachment_dir_for_db

    legacy = tmp_path / "attachments"
    legacy.mkdir()
    (legacy / "a.pdf").write_bytes(b"pdf")
    (tmp_path / "sessions.db").write_bytes(b"db")

    resolved = attachment_dir_for_db(tmp_path / "Sessions.db")

    assert resolved == legacy


# ---------------------------------------------------------------- Lead 兜底
def test_lead_synthesis_falls_back_on_llm_error():
    from core.llm_client import LLMError
    from swarm.lead_agent import LeadAgent
    from swarm.shared_context import SharedContext, SubTask

    class FailingLLM:
        async def chat(self, messages, **kwargs):
            raise LLMError("llm_rate_limit", "请求过于频繁", retryable=True)

    context = SharedContext(session_id="r33-fallback")
    subtask = SubTask(
        id="s1",
        type="consultation",
        description="给出建议",
        assigned_agent="consultation_agent",
    )
    context.add_subtask(subtask)
    assert context.start_subtask("s1")
    context.complete_subtask(
        "s1", "consultation_agent", {"answer": "各科室原始建议内容"}
    )

    agent = LeadAgent(llm_client=FailingLLM())
    text = asyncio.run(agent.synthesize_results("测试问题", context))

    # 汇总调用失败不能丢弃已完成的科室意见
    assert "各科室原始意见" in text
    assert "各科室原始建议内容" in text


# ---------------------------------------------------------------- 前端静态断言
def test_session_flow_guards_are_present():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "let pendingSessionReset = false;" in app_js
    assert "safeSessionGet('medix.session.tab')" in app_js
    assert "else if (sessionId && !safeSessionGet('medix.session.tab'))" in app_js

    start = app_js.index("async function startNewSession()")
    body = app_js[start:start + 1800]
    assert "seq !== openSessionSeq || busy" in body

    detach_start = app_js.index("async function detachSessionForProfileChange()")
    detach = app_js[detach_start:detach_start + 900]
    assert "pendingSessionReset = true" in detach

    send_start = app_js.index("async function send(question)")
    send_body = app_js[send_start:send_start + 1200]
    assert "if (pendingSessionReset)" in send_body
    assert "clearPersistedSessionId()" in send_body


def test_restore_context_does_not_overwrite_draft():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "options.restoreContext !== false" in app_js
    restore = app_js[
        app_js.index("async function restoreCurrentSession"):
        app_js.index("function scheduleAbortReconcile")
    ]
    assert "{ restoreContext: false }" in restore


def test_attachment_limits_match_server():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "const ATTACHMENT_LINK_CHUNK = 200;" in app_js
    assert "const ATTACHMENT_EXPORT_MAX_SELECTED = 200;" in app_js
    assert "start += ATTACHMENT_LINK_CHUNK" in app_js
    assert "attachmentSelectedIds.size > ATTACHMENT_EXPORT_MAX_SELECTED" in app_js
    assert "一次最多导出 ${ATTACHMENT_EXPORT_MAX_SELECTED} 份附件" in app_js


def test_memory_scope_and_tombstone_wiring_static():
    coordinator = (ROOT / "swarm" / "swarm_coordinator.py").read_text(
        encoding="utf-8"
    )
    assert "profile_rebinding_blocked" in coordinator
    write_scope = coordinator[
        coordinator.index("async def _write_scope()"):
        coordinator.index("async def _write_scope()") + 800
    ]
    assert "if patient_profile:" in write_scope
    gone = coordinator[
        coordinator.index("async def _profile_gone()"):
        coordinator.index("async def _profile_gone()") + 2400
    ]
    assert "profile_rebinding_blocked" in gone

    session_store = (ROOT / "memory" / "session_store.py").read_text(
        encoding="utf-8"
    )
    assert "def profile_rebinding_blocked(" in session_store
    assert "[:64]" not in session_store[
        session_store.index("def queue_memory_cleanup("):
        session_store.index("def queue_memory_cleanup(") + 900
    ]

    lead = (ROOT / "swarm" / "lead_agent.py").read_text(encoding="utf-8")
    except_pos = lead.index("except LLMError as e:")
    fallback_pos = lead.index("return fallback_answer()", except_pos)
    assert except_pos < fallback_pos
