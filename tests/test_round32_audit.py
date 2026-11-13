"""第三十二轮审查：对比视图/通知调度/搜索适配/来源/配置/备份/a11y/校验。"""
import asyncio
import hashlib
import importlib.util
import json
import os
import time
from datetime import datetime
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


# ---------------------------------------------------------------- 时区越界
def test_canonical_time_handles_out_of_range_years():
    from memory.session_store import _canonical_local_time

    # Windows 的 localtime 不支持 1970 年前带时区时间：必须回退而不是抛异常
    old = _canonical_local_time("1965-06-01T00:00:00+08:00")
    assert old is not None
    assert old.startswith("1965-0")

    # 正时区把 9999 年换算到 10000 年会抛 OverflowError：按无效输入处理
    assert _canonical_local_time("9999-12-31T23:00:00+00:00") is None
    assert _canonical_local_time("9999-01-01T00:00:00+00:00") is None


def test_metrics_accept_old_timezone_dates(client_and_store):
    client, _store = client_and_store

    response = client.post("/api/metrics", json={
        "metric": "weight",
        "value1": 60,
        "measured_at": "1965-06-01T00:00:00+08:00",
    })

    assert response.status_code == 200

    response = client.post("/api/metrics", json={
        "metric": "weight",
        "value1": 60,
        "measured_at": "9999-12-31T23:00:00+00:00",
    })
    # 年份越界应是 400（无效时间），而不是 500
    assert response.status_code == 400


# ---------------------------------------------------------------- 校验和
def test_backup_checksum_streaming_matches_legacy_format():
    from memory.session_store import (
        _BACKUP_CHECKSUM_FIELDS,
        _backup_checksum,
        _canonical_json_value,
    )

    payload = {
        "version": 1,
        "sessions": [{
            "id": "s1",
            "title": "体重 70.0kg",
            "messages": [{
                "id": 1,
                "role": "user",
                "content": "问题",
                "metadata": {
                    "value": 70.0,
                    "ratio": 0.25,
                    "bad": float("nan"),
                },
            }],
        }],
        "profiles": [{"name": "妈妈", "meta": {"n": 3.0, "s": "文本"}}],
    }
    body = {
        key: payload[key]
        for key in _BACKUP_CHECKSUM_FIELDS
        if key in payload
    }
    canonical = json.dumps(
        _canonical_json_value(body),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected = "sha256:" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()

    assert _backup_checksum(payload) == expected


# ---------------------------------------------------------------- 提醒清理
def test_bulk_delete_only_done_skips_reopened(store):
    first = store.add_reminder("服药", "2026-12-01")
    second = store.add_reminder("复诊", "2026-12-02")
    for item in (first, second):
        assert store.complete_reminder(item["id"])

    assert store.delete_reminders_bulk([first["id"]], True) == 1
    store.reopen_reminder(second["id"])
    # 其它设备恢复的提醒仍有 done=0，清理接口不能删除
    assert store.delete_reminders_bulk([second["id"]], True) == 0
    assert any(
        item["id"] == second["id"] for item in store.list_reminders(True)
    )


def test_bulk_delete_endpoint_skips_reopened(client_and_store):
    client, store = client_and_store
    ids = [
        client.post("/api/reminders", json={
            "title": f"提醒{index}", "due_at": "2026-12-01",
        }).json()["reminder"]["id"]
        for index in range(2)
    ]
    store.complete_reminder(ids[0])
    store.complete_reminder(ids[1])
    store.reopen_reminder(ids[1])

    response = client.post(
        "/api/reminders/bulk_delete", json={"ids": ids}
    )

    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    remaining = {item["id"] for item in store.list_reminders(True)}
    assert ids[1] in remaining
    assert ids[0] not in remaining


# ---------------------------------------------------------------- 删除会话门禁
def test_chat_rejects_deleted_session(client_and_store):
    client, store = client_and_store
    store.save_exchange("gone", "问题", "回答")
    store.delete_session("gone")

    response = client.post("/api/chat", json={
        "question": "继续", "session_id": "gone",
    })

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "session_deleted"

    response = client.post("/api/chat/stream", json={
        "question": "继续", "session_id": "gone",
    })
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_deleted"


# ---------------------------------------------------------------- 配置加载
def test_config_loader_uses_explicit_path(tmp_path, monkeypatch):
    import core.config_loader as loader

    config_file = tmp_path / "medix_config.py"
    config_file.write_text(
        "LLM_CONFIG = {'api_key': 'x'}\n"
        "MEM0_CONFIG = {'api_key': 'y'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIX_CONFIG", str(config_file))
    monkeypatch.setattr(loader, "_MODULE_CACHE", {})

    assert loader.load_config_value("LLM_CONFIG")["api_key"] == "x"
    assert loader.load_config_value("MEM0_CONFIG")["api_key"] == "y"
    assert loader.load_config_value("NOT_EXIST") is None
    assert loader.describe_config_source().endswith("medix_config.py")


def test_llm_config_empty_env_falls_back_to_defaults():
    """空字符串的 MEDIX_MODEL/MEDIX_BASE_URL 不能覆盖内置默认值。"""
    import subprocess
    import sys

    env = dict(os.environ)
    env["MEDIX_MODEL"] = ""
    env["MEDIX_BASE_URL"] = "  "
    env["MEDIX_API_KEY"] = ""
    env.pop("MEDIX_CONFIG", None)
    code = (
        "from core.llm_client import LLM_CONFIG;"
        "print(repr(LLM_CONFIG['model_name']));"
        "print(repr(LLM_CONFIG['base_url']))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-2] not in ("''", '""')
    assert lines[-1].strip("'\"").startswith("http")


def test_sanitize_base_url_drops_credentials_on_invalid_port():
    from core.security import redact_url_queries, sanitize_base_url

    sanitized = sanitize_base_url(
        "https://alice:s3cret@gw.example.com:8000x/v1?token=abc"
    )
    assert "s3cret" not in sanitized
    assert "token=abc" not in sanitized
    assert "8000x" in sanitized
    assert sanitized == "https://gw.example.com:8000x/v1"

    assert redact_url_queries(
        "error sending request for url (https://x.test/search?q=%E6%82%A3)"
    ) == "error sending request for url (https://x.test/search?<redacted>)"


# ---------------------------------------------------------------- 搜索
def test_web_search_no_results_exception_not_retried(monkeypatch):
    import research.web_search as ws

    calls = {"count": 0}

    class FakeDDGS:
        def __init__(self):
            calls["count"] += 1

        def text(self, *args, **kwargs):
            raise RuntimeError("No results found.")

    monkeypatch.setattr(ws, "DDGS", FakeDDGS)
    monkeypatch.setattr(ws, "DDGS_AVAILABLE", True)

    results = asyncio.run(
        ws.WebSearchTool().search("高血压", retry_count=2)
    )

    assert results == []
    # 只有一轮后端尝试；空结果不再触发 3 轮重试
    assert calls["count"] == len(ws._DDGS_BACKENDS)


def test_web_search_redacts_query_from_error_logs(monkeypatch):
    import research.web_search as ws

    class FakeLogger:
        def __init__(self):
            self.messages = []

        def _record(self, message, *args, **kwargs):
            self.messages.append(str(message))

        info = debug = warning = error = _record

    fake_logger = FakeLogger()
    called = {"count": 0}

    class FakeDDGS:
        def text(self, *args, **kwargs):
            called["count"] += 1
            raise RuntimeError(
                "error sending request for url "
                "(https://search.example/s?q=%E6%82%A3%E8%80%85%E7%97%85%E5%8F%B2)"
            )

    monkeypatch.setattr(ws, "DDGS", FakeDDGS)
    monkeypatch.setattr(ws, "DDGS_AVAILABLE", True)
    monkeypatch.setattr(ws, "logger", fake_logger)

    results = asyncio.run(
        ws.WebSearchTool().search("患者病史", retry_count=0)
    )

    assert results == []
    joined = "\n".join(fake_logger.messages)
    assert "%E6%82%A3" not in joined
    assert "<redacted>" in joined
    # 传输错误仍按 retry_count 重试
    assert called["count"] == len(ws._DDGS_BACKENDS)


def test_fetch_target_blocks_internal_addresses():
    import research.web_search as ws

    for url in (
        "http://127.0.0.1:8123/secret",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.2/",
        "http://localhost/admin",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ):
        with pytest.raises(ws.UnsafeFetchTarget):
            ws._validate_fetch_target(url)

    # 公网 IP 字面量不需要 DNS，可离线校验
    assert ws._validate_fetch_target("https://8.8.8.8/") == "https://8.8.8.8/"


def test_extract_html_text_handles_gbk_without_header():
    import research.web_search as ws

    if ws.BeautifulSoup is None:
        pytest.skip("beautifulsoup4 未安装")
    body = (
        '<html><head><meta charset="gbk"></head>'
        "<body><p>高血压患者</p></body></html>"
    ).encode("gbk")
    text = ws._extract_html_text(body, None, 100)

    assert "高血压患者" in text


# ---------------------------------------------------------------- 来源一致
def test_search_knowledge_numbering_matches_sources(monkeypatch):
    module = _load_script(
        ".claude/skills/search-knowledge/script/search.py",
        "search_knowledge_round32",
    )

    class FakeKB:
        def search(self, query, top_k=5, filter_type=None):
            return [
                {
                    "id": index,
                    "content": f"块{index}",
                    "metadata": {
                        "filename": "a.md" if index < 3 else "b.md",
                        "disease": "高血压",
                    },
                    "score": 0.9 - index * 0.1,
                }
                for index in range(5)
            ]

    monkeypatch.setattr(module, "get_knowledge_base", lambda: FakeKB())
    result = asyncio.run(module.search_knowledge("高血压", max_results=5))

    # 正文编号与来源卡片基于同一份按文档去重的列表
    assert result["total_found"] == len(result["sources"]) == 2
    assert "【结果 1】" in result["answer"]
    assert "【结果 2】" in result["answer"]
    assert "【结果 3】" not in result["answer"]


def test_skill_footers_do_not_claim_unused_knowledge_base():
    risk = _load_script(
        ".claude/skills/assess-risk/script/risk.py", "risk_round32"
    )
    emergency = risk.format_assessment("胸痛", "emergency", [], "拨打120", None)
    assert "医学知识库（Milvus RAG）" not in emergency
    assert "风险规则引擎" in emergency
    with_kb = risk.format_assessment("胸痛", "low", [], "观察", "建议休息")
    assert "医学知识库（Milvus RAG）" in with_kb

    symptoms = _load_script(
        ".claude/skills/analyze-symptoms/script/symptoms.py",
        "symptoms_round32",
    )
    plain = symptoms.format_analysis("皮肤瘙痒", [], [], [])
    assert "医学知识库（Milvus RAG）" not in plain
    assert "症状模式分析" in plain
    with_kb = symptoms.format_analysis(
        "皮肤瘙痒", [], [], [{"disease": "皮炎", "info": "保持干燥"}]
    )
    assert "医学知识库（Milvus RAG）" in with_kb


def test_synthesizer_keeps_real_kb_metadata():
    from research.evidence_synthesizer import EvidenceSynthesizer

    synthesizer = EvidenceSynthesizer.__new__(EvidenceSynthesizer)
    from types import SimpleNamespace

    kb_results = [{
        "id": 1,
        "content": "限盐并规律运动可以降低血压",
        "metadata": {"filename": "guideline.md", "disease": "高血压"},
        "score": 0.62,
    }]
    response = (
        "【关键发现】\n1. 限盐有效\n"
        "【综合总结】\n证据一致。\n"
        "【证据等级】\nB\n"
        "【置信度】\n0.8\n"
    )
    report = synthesizer._parse_response("高血压", response, [], kb_results)

    assert report.sources[0]["filename"] == "guideline.md"
    assert report.sources[0]["score"] == 0.62
    assert "限盐" in report.sources[0]["snippet"]
    # 报告级置信度不再冒充逐条来源的相关度
    assert "confidence" not in report.sources[0]


# ---------------------------------------------------------------- 备份
def test_backup_dir_follows_session_db(tmp_path, monkeypatch):
    from core.backup import DEFAULT_BACKUP_DIR, get_backup_dir

    monkeypatch.delenv("MEDIX_BACKUP_DIR", raising=False)
    monkeypatch.delenv("MEDIX_SESSION_DB", raising=False)
    assert get_backup_dir() == DEFAULT_BACKUP_DIR

    db_path = tmp_path / "child.db"
    monkeypatch.setenv("MEDIX_SESSION_DB", str(db_path))
    assert get_backup_dir() == tmp_path / "child.db-backups"


def test_ensure_backup_target_rejects_file(tmp_path):
    from core.backup import ensure_backup_target_ready

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        ensure_backup_target_ready(blocker / "sub")


def test_auto_backup_wait_ignores_future_backups(tmp_path, monkeypatch):
    import api_server
    from core.backup import create_backup

    monkeypatch.setenv("MEDIX_BACKUP_DIR", str(tmp_path))
    path = create_backup({"n": 1}, directory=tmp_path, keep=5)
    future = time.time() + 365 * 24 * 3600
    os.utime(path, (future, future))

    # 时钟回拨后不能等墙钟追平（可能长达一年），应立即补一份备份
    assert api_server.auto_backup_wait_seconds(6) == 0.0


def test_prune_removes_future_dated_backups_first(tmp_path):
    from core.backup import _prune_backups

    normal = tmp_path / "medix-backup-20261113-090000.json"
    future_old = tmp_path / "medix-backup-20270101-090000.json"
    future_new = tmp_path / "medix-backup-20270102-090000.json"
    for path in (normal, future_old, future_new):
        path.write_text("{}", encoding="utf-8")
    future_ts = time.time() + 365 * 24 * 3600
    os.utime(future_old, (future_ts, future_ts))
    os.utime(future_new, (future_ts, future_ts))

    _prune_backups(tmp_path, keep=2)

    # 真正的（时钟校正后写入的）备份必须保留；未来时间戳文件先被轮转
    assert normal.exists()
    remaining = list(tmp_path.glob("medix-backup-*.json"))
    assert len(remaining) == 2


def test_backups_api_marks_clock_anomaly(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api_server

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("MEDIX_BACKUP_DIR", str(tmp_path))
    future_file = tmp_path / "medix-backup-20270101-000000.json"
    future_file.write_text("{}", encoding="utf-8")
    future_ts = time.time() + 365 * 24 * 3600
    os.utime(future_file, (future_ts, future_ts))

    data = TestClient(api_server.app).get("/api/backups").json()

    assert data["backups"][0]["clock_anomaly"] is True


def test_kb_status_recovers_after_rebuild(client_and_store, monkeypatch):
    import api_server
    import knowledge.service as knowledge_service

    class FakeService:
        def rebuild(self):
            return {"documents": 1}

    monkeypatch.setattr(
        knowledge_service, "get_knowledge_service", lambda: FakeService()
    )
    api_server.state.kb_status = "failed"
    api_server.state.kb_error = "startup model download failed"

    client, _store = client_and_store
    response = client.post("/api/knowledge/rebuild")

    assert response.status_code == 200
    assert api_server.state.kb_status == "ready"
    assert api_server.state.kb_error is None


def test_app_state_kb_helpers():
    import api_server

    state = api_server.AppState()
    state.set_kb_failed(ValueError("startup model download failed"))
    assert state.kb_status == "failed"
    assert "download" in state.kb_error
    state.set_kb_ready()
    assert state.kb_status == "ready"
    assert state.kb_error is None


# ---------------------------------------------------------------- 前端静态断言
def test_restore_session_handles_deleted_404():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("async function restoreCurrentSession")
    body = app_js[start:app_js.index("function scheduleAbortReconcile")]

    assert "detail.code === 'session_deleted'" in body
    assert "clearDeletedSession(" in body

    helper = app_js[
        app_js.index("function clearDeletedSession("):
        app_js.index("function clearDeletedSession(") + 900
    ]
    assert "clearPersistedSessionId()" in helper
    assert "setSessionUrl(null)" in helper


def test_stored_messages_payload_keeps_safety_metadata():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function renderStoredMessages(")
    body = app_js[start:start + 2200]

    assert "medication_safety: metadata.medication_safety || {}" in body
    assert "usage: metadata.usage || null" in body
    assert "llm_calls: metadata.llm_calls || 0" in body


def test_compare_requests_are_sequenced():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("async function openCompare(")
    body = app_js[start:start + 1600]

    assert "sessionCompareRequestSeq" in body
    assert "requestSeq !== sessionCompareRequestSeq" in body
    assert "status.isConnected" in body


def test_reminders_refresh_on_day_change():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "function refreshRemindersOnDayChange()" in app_js
    assert "lastReminderRefreshDate = todayLocalDate();" in app_js
    assert app_js.count("addEventListener('visibilitychange'") >= 2
    assert "void refreshRemindersOnDayChange();" in app_js


def test_clear_done_refetches_server_state():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("async function clearDoneReminders()")
    body = app_js[start:start + 1400]

    assert "include_done: 'true'" in body
    assert "if (done === null)" in body


def test_health_lists_restore_focus():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    for list_name in (
        "metricsList", "symptomList", "vaccinationList", "appointmentList",
        "labList", "allergyList", "careList", "visitList", "conditionList",
    ):
        assert f"preserveListFocus(els.{list_name});" in app_js
    assert "function restoreListFocusState(" in app_js
    assert "focusFirstInDrawer(root)" in app_js
    # 删除用药后同样按索引回退，焦点不掉到 body
    assert "Math.max(Number(state.index) || 0, 0)" in app_js


def test_sw_notification_message_acknowledged():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    sw_js = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")

    assert "medix-open-reminders-ack" in app_js
    assert "waitForReminderAck" in sw_js
    assert "targetWindow.postMessage" in sw_js
