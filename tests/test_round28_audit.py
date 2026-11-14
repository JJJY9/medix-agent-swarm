"""第二十八轮审查：上下文优先级、附件目录、隐私与运行时回归。"""
from pathlib import Path

import pytest

from core.context import MAX_CONTEXT_LENGTH, format_context


def test_long_history_keeps_safety_fields():
    context = {
        "history": "高血压病史。" * 400,
        "allergies": "青霉素（重度）",
        "active_medications": "阿司匹林、氨氯地平",
        "recent_metrics": "血压 160/95",
        "pending_reminders": "复诊预约 12-01",
        "age": "66",
        "recent_history": [
            {"role": "user", "content": f"第{index}轮追问"}
            for index in range(10)
        ],
    }

    text = format_context(context, include_internal=True)

    assert len(text) <= MAX_CONTEXT_LENGTH
    assert "过敏史：青霉素（重度）" in text
    assert "阿司匹林、氨氯地平" in text
    assert "160/95" in text
    assert "复诊预约 12-01" in text
    # 长病史被单独截断，而不是把安全字段挤出提示词
    assert "既往病史" in text


def test_long_history_without_internal_keeps_allergies():
    text = format_context({
        "history": "长" * 5000,
        "allergies": "磺胺类药物过敏",
    })

    assert len(text) <= MAX_CONTEXT_LENGTH
    assert "过敏史：磺胺类药物过敏" in text
    assert "…（已截断）" in text


def test_small_max_length_still_renders_priority_fields():
    text = format_context(
        {
            "allergies": "青霉素",
            "medications": "阿司匹林",
            "history": "长" * 500,
        },
        max_length=400,
    )

    assert len(text) <= 400
    assert "过敏史：青霉素" in text
    assert "阿司匹林" in text


def test_unknown_keys_still_rendered_when_room():
    text = format_context({"custom_field": "自定义信息", "age": "30"})

    assert "自定义信息" in text
    assert "年龄：30" in text


def test_attachment_dir_differs_for_same_directory_databases(tmp_path):
    from core.storage_paths import attachment_dir_for_db

    main_db = tmp_path / "sessions.db"
    other_db = tmp_path / "test.db"

    main_dir = attachment_dir_for_db(main_db)
    other_dir = attachment_dir_for_db(other_db)

    assert main_dir == tmp_path / "attachments"
    assert other_dir != main_dir
    assert other_dir.name.startswith("test")


def test_attachment_cleanup_does_not_touch_other_database(tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    main_db = tmp_path / "sessions.db"
    other_db = tmp_path / "test.db"
    main_store = SessionStore(str(main_db))
    try:
        attachment = main_store.add_attachment(
            "妈妈", "visit", 0, "化验单.pdf", "abc123.pdf",
            "application/pdf", 5,
        )
        assert attachment is not None
        attach_dir = tmp_path / "attachments"
        attach_dir.mkdir(exist_ok=True)
        target = attach_dir / "abc123.pdf"
        target.write_bytes(b"%PDF-1.4")

        other_store = SessionStore(str(other_db))
        try:
            monkeypatch = pytest.MonkeyPatch()
            monkeypatch.setattr(
                memory, "get_session_store", lambda: other_store
            )
            monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
            try:
                client = TestClient(api_server.app)
                integrity = client.get("/api/attachments/integrity").json()
                assert integrity["orphan_count"] == 0

                cleanup = client.post(
                    "/api/attachments/cleanup",
                    json={"confirm": True, "expected_orphans": 0},
                )
                assert cleanup.status_code == 200
            finally:
                monkeypatch.undo()
        finally:
            other_store.close()

        assert target.exists()
        assert main_store.get_attachment(attachment["id"]) is not None
    finally:
        main_store.close()


@pytest.fixture
def store(tmp_path):
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        yield session_store
    finally:
        session_store.close()


def test_visit_profile_change_moves_attachments(store):
    visit = store.add_visit_record("妈妈", visit_date="2026-11-01")
    attachment = store.add_attachment(
        "妈妈", "visit", visit["id"], "出院小结.pdf", "visit-slip.pdf",
        "application/pdf", 10,
    )

    store.update_visit_record_partial(visit["id"], profile="爸爸")

    moved = store.get_attachment(attachment["id"])
    assert moved["profile"] == "爸爸"


def test_lab_profile_change_moves_attachments(store):
    lab = store.add_lab_result(
        "妈妈", "ALT", 55, "U/L", tested_at="2026-11-01",
    )
    attachment = store.add_attachment(
        "妈妈", "lab", lab["id"], "化验单.pdf", "lab-slip.pdf",
        "application/pdf", 10,
    )

    store.update_lab_result_partial(lab["id"], profile="爸爸")

    moved = store.get_attachment(attachment["id"])
    assert moved["profile"] == "爸爸"


def test_insecure_lan_rejected_without_token(monkeypatch):
    from fastapi.testclient import TestClient

    import api_server

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MEDIX_ALLOW_INSECURE_LAN", raising=False)

    lan_client = TestClient(
        api_server.app,
        base_url="http://192.168.1.50",
        client=("192.168.1.50", 50000),
    )
    blocked = lan_client.get("/api/sessions")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "insecure_lan_rejected"

    # 本机访问不变
    assert TestClient(api_server.app).get("/api/sessions").status_code == 200

    # 显式允许可信内网后放行
    monkeypatch.setenv("MEDIX_ALLOW_INSECURE_LAN", "1")
    assert lan_client.get("/api/sessions").status_code == 200


def test_blocked_ip_skips_token_comparison(monkeypatch):
    from fastapi.testclient import TestClient

    import api_server
    from core.ratelimit import SlidingWindowLimiter

    token = "a-secure-random-token-123"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(2, window=300),
    )
    comparisons = {"count": 0}
    original = api_server.is_authorized

    def counting(request):
        comparisons["count"] += 1
        return original(request)

    monkeypatch.setattr(api_server, "is_authorized", counting)
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("203.0.113.9", 50000),
    )
    wrong = {"X-Medix-Token": "bad-guess"}

    assert client.get("/api/sessions", headers=wrong).status_code == 401
    assert client.get("/api/sessions", headers=wrong).status_code == 401
    assert client.get("/api/sessions", headers=wrong).status_code == 429
    after_lockout = comparisons["count"]
    blocked = client.get(
        "/api/sessions", headers={"X-Medix-Token": token}
    )

    assert blocked.status_code == 429
    # 锁定期内不再执行令牌比较
    assert comparisons["count"] == after_lockout


def test_degraded_answer_detection():
    from core.agent_loop import _is_placeholder_answer
    from swarm.lead_agent import is_degraded_answer

    assert is_degraded_answer("抱歉，Swarm 未能提供有效分析结果。")
    assert is_degraded_answer(
        "抱歉，由于系统响应超时，所有 Agent 均未能在规定时间内完成分析。"
    )
    assert not is_degraded_answer("建议低盐饮食并规律监测血压。")

    assert _is_placeholder_answer("")
    assert _is_placeholder_answer(
        "抱歉，未能生成有效答复，请稍后重试或简化问题。"
    )
    assert _is_placeholder_answer(
        "[回答内容达到长度上限，已截断。建议简化问题后重试。]"
    )
    assert not _is_placeholder_answer(
        "建议低盐饮食。"
        "[回答内容达到长度上限，已截断。建议简化问题后重试。]"
    )


def test_symptom_analysis_respects_negation(monkeypatch):
    import asyncio
    from importlib.util import module_from_spec, spec_from_file_location

    root = Path(__file__).resolve().parents[1]
    path = (
        root / ".claude" / "skills" / "analyze-symptoms"
        / "script" / "symptoms.py"
    )
    spec = spec_from_file_location("round28_symptoms", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    result = asyncio.run(
        module.analyze_symptoms("没有咳嗽，没有胸痛，无腹痛")
    )

    assert result["patterns"] == []
    assert result["possible_diseases"] == []


def _fake_completion_client(response):
    import types

    from core.llm_client import LLMClient

    client = LLMClient.__new__(LLMClient)
    client.model_type = "openai_compatible"
    client.model_name = "test-model"
    client.temperature = 0.2
    client.max_tokens = 100

    class Completions:
        async def create(self, **kwargs):
            return response

    client.client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=Completions())
    )
    return client


def test_chat_with_usage_marks_truncated_answer():
    import asyncio
    import types

    choice = types.SimpleNamespace(
        message=types.SimpleNamespace(content="建议低盐饮食。"),
        finish_reason="length",
    )
    response = types.SimpleNamespace(choices=[choice], usage=None)
    client = _fake_completion_client(response)
    reason = {}

    content, _usage = asyncio.run(
        client.chat_with_usage(
            [{"role": "user", "content": "x"}],
            finish_reason_out=reason,
        )
    )

    assert "长度上限" in content
    assert reason["finish_reason"] == "length"


def test_chat_with_usage_stop_adds_no_notice():
    import asyncio
    import types

    choice = types.SimpleNamespace(
        message=types.SimpleNamespace(content="多喝水。"),
        finish_reason="stop",
    )
    response = types.SimpleNamespace(choices=[choice], usage=None)
    client = _fake_completion_client(response)

    content, _usage = asyncio.run(
        client.chat_with_usage([{"role": "user", "content": "x"}])
    )

    assert content == "多喝水。"


def test_chat_with_tools_skips_malformed_members():
    import asyncio
    import types

    malformed = types.SimpleNamespace(id="bad")
    valid = types.SimpleNamespace(
        id="ok",
        function=types.SimpleNamespace(
            name="search_knowledge", arguments={"query": "高血压"}
        ),
    )
    message = types.SimpleNamespace(
        content="", tool_calls=[malformed, valid]
    )
    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=message, finish_reason="tool_calls",
            )
        ],
        usage=None,
    )
    client = _fake_completion_client(response)

    result = asyncio.run(client.chat_with_tools(messages=[]))

    assert [call.name for call in result.tool_calls] == ["search_knowledge"]
    assert result.tool_calls[0].arguments == {"query": "高血压"}


def test_tool_call_arguments_accept_objects():
    from core.llm_client import parse_tool_call_arguments

    assert parse_tool_call_arguments({"query": "高血压"}) == {
        "query": "高血压"
    }
    assert parse_tool_call_arguments('{"query": "高血压"}') == {
        "query": "高血压"
    }
