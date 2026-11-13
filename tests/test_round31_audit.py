"""第三十一轮审查：计算器重置/每日对比/用量合并/导出审计/导入校验。"""
import json
from datetime import datetime, timedelta
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


def test_usage_by_session_includes_failed_events(store):
    store.save_exchange(
        "s1", "问题", "回答",
        metadata={"usage": {"total_tokens": 100}, "llm_calls": 1},
    )
    store.record_usage_event(
        "s1", "妈妈",
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        llm_calls=2,
    )

    stats = store.usage_by_session(["s1"])

    assert stats["s1"]["usage"]["total_tokens"] == 115
    assert stats["s1"]["llm_calls"] == 3


def test_usage_totals_and_session_totals_include_events(store):
    store.save_exchange(
        "s1", "问题", "回答",
        metadata={"usage": {"total_tokens": 100}, "llm_calls": 1},
    )
    store.record_usage_event(
        "s1", "妈妈", {"total_tokens": 15}, llm_calls=2
    )

    totals = store.usage_totals()
    session_totals = store.session_totals()

    assert totals["usage"]["total_tokens"] == 115
    assert totals["llm_calls"] == 3
    assert session_totals["usage"]["total_tokens"] == 115
    assert session_totals["llm_calls"] == 3


def test_daily_usage_includes_events_for_favorites(store):
    store.save_exchange("fav", "问题", "回答")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET favorite = 1 WHERE id = 'fav'"
        )
    store.record_usage_event(
        "fav", "妈妈", {"total_tokens": 42}, llm_calls=1
    )

    data = store.daily_usage(days=7, favorites_only=True)

    assert data["totals"]["usage"]["total_tokens"] == 42


def test_prune_table_keeps_latest_rows(store):
    with store._lock, store._conn:
        for index in range(6):
            store._conn.execute(
                "INSERT INTO audit_log(action, target, detail, created_at)"
                " VALUES (?, '', '', ?)",
                (f"a{index}", "2026-11-01T00:00:00"),
            )
        store._prune_table_locked("audit_log", 3)
        rows = store._conn.execute(
            "SELECT action FROM audit_log ORDER BY id"
        ).fetchall()
    assert [row["action"] for row in rows] == ["a3", "a4", "a5"]


def test_import_rejects_invalid_repeat_window(store):
    result = store.import_data({
        "reminders": [{
            "profile": "妈妈", "title": "服药", "due_at": "2026-12-10",
            "repeat_days": 7, "repeat_until": "2026-12-01",
            "created_at": "2026-11-01T00:00:00",
        }],
    })

    assert result["reminders"] == 0
    assert result["invalid"] == 1


def test_import_clears_memory_cleanup_queue(store):
    store.save_profile("妈妈", {"sex": "女"})
    backup = store.export_data()
    store.delete_profile("妈妈")
    store.queue_memory_cleanup([("妈妈", "")])
    with store._lock:
        before = store.list_memory_cleanup()
    assert before

    store.import_data(backup)

    with store._lock:
        after = store.list_memory_cleanup()
    assert after == []


def test_reminder_content_match_not_cross_profile(store):
    # 目标库已有另一位成员的同秒同到期日提醒
    store.import_data({
        "reminders": [{
            "profile": "爸爸", "title": "爸爸提醒",
            "due_at": "2026-12-01", "created_at": "2026-11-01T00:00:00",
        }],
    })

    result = store.import_data({
        "reminders": [{
            "id": 2,
            "profile": "妈妈", "title": "妈妈提醒",
            "due_at": "2026-12-01", "created_at": "2026-11-01T00:00:00",
        }],
    })

    assert result["reminders"] == 1
    assert sorted(
        row["profile"] for row in store.list_reminders(True)
    ) == ["妈妈", "爸爸"]


def test_dedicated_attachment_dir_distinguishes_suffix(tmp_path):
    from core.storage_paths import attachment_dir_for_db

    db_dir = attachment_dir_for_db(tmp_path / "X.db")
    sqlite_dir = attachment_dir_for_db(tmp_path / "X.sqlite")

    assert db_dir != sqlite_dir
    assert "X.db" in db_dir.name
    assert "X.sqlite" in sqlite_dir.name


def test_family_compare_uses_daily_totals(client_and_store):
    client, store = client_and_store
    store.set_metric_target("妈妈", "steps", 8000)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    store.add_metric(
        "妈妈", "steps", 4000, measured_at=f"{yesterday}T08:00"
    )
    store.add_metric(
        "妈妈", "steps", 5000, measured_at=f"{yesterday}T18:00"
    )

    data = client.get(
        "/api/family/compare", params={"metric": "steps", "days": 7}
    ).json()

    row = next(item for item in data["profiles"] if item["name"] == "妈妈")
    assert row["count"] == 1
    assert row["avg_value1"] == 9000
    assert row["within_target"] == 1
    assert row["latest"]["value1"] == 9000


def test_family_overview_includes_unnamed_records(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    today = datetime.now().replace(
        hour=9, minute=0
    ).isoformat(timespec="minutes")
    store.add_metric("", "bp", 130, 85, measured_at=today)

    data = client.get("/api/family/overview").json()

    unnamed = next(
        item for item in data["profiles"]
        if item.get("display_name") == "未指定"
    )
    assert unnamed["metrics"]
    assert unnamed["metrics"][0]["metric"] == "bp"


def test_dossier_recent_metrics_show_daily_total(client_and_store):
    client, store = client_and_store
    store.set_metric_target("妈妈", "steps", 8000)
    today = datetime.now()
    store.add_metric(
        "妈妈", "steps", 4000,
        measured_at=today.replace(hour=8, minute=0).isoformat(
            timespec="minutes"
        ),
    )
    store.add_metric(
        "妈妈", "steps", 5000,
        measured_at=today.replace(hour=18, minute=0).isoformat(
            timespec="minutes"
        ),
    )

    body = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    ).text
    metrics_section = body.split("## 最近指标", 1)[1].split("##", 1)[0]
    steps_line = next(
        line for line in metrics_section.splitlines()
        if line.startswith("- 步数")
    )
    assert "当日累计 9000" in steps_line


def test_sensitive_exports_write_audit(client_and_store):
    client, store = client_and_store
    store.save_exchange("s1", "问题", "回答")

    client.get("/api/sessions/export.csv")
    client.get("/api/family/overview/export.csv")

    actions = {
        row["action"] for row in store.list_audit_log(limit=50)
    }
    assert "session.export.csv" in actions
    assert "family.overview.export.csv" in actions


def test_calc_forms_reset_on_profile_switch():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "function resetCalcForms()" in app_js
    assert "calcProfileName !== currentCalcProfile" in app_js
    # CURB/Child-Pugh 的手工项在缺化验提前返回前已重置
    curb = app_js[
        app_js.index("async function prefillCurb65FromLab"):
        app_js.index("async function prefillCurb65FromLab") + 1200
    ]
    assert curb.index("calcCurbConfusion.checked = false") < curb.index(
        "if (!ureaValue)"
    )
    child = app_js[
        app_js.index("async function prefillChildPughFromLab"):
        app_js.index("async function prefillChildPughFromLab") + 1200
    ]
    assert child.index("calcChildAscites.value = '1'") < child.index(
        "if (missing.length)"
    )


def test_apri_uln_uses_unit_conversion():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("async function prefillApriFromLab")
    body = app_js[start:start + 2000]
    assert "labAstAltUL({ value: refHigh, unit: ast && ast.unit })" in body


def test_open_session_clears_deleted_persisted_id():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("async function openSession(id)")
    body = app_js[start:start + 2600]
    assert "error.sessionDeleted = true" in body
    # 清理逻辑抽到 clearDeletedSession，与启动/深链恢复路径共用
    assert "clearDeletedSession(" in body
    helper = app_js[
        app_js.index("function clearDeletedSession("):
        app_js.index("function clearDeletedSession(") + 900
    ]
    assert "clearPersistedSessionId()" in helper


def test_bulk_requests_are_chunked():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "async function postBulkSessions(" in app_js
    assert app_js.count("postBulkSessions(") >= 4
    assert "for (let start = 0; start < ids.length; start += 500)" in app_js
    assert "if (append && historyItemMap.has(item.id))" in app_js


def test_family_display_prefers_daily_total():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "profile.display_name || profile.name" in app_js
    assert "${hasDaily ? '今日 ' : ''}" in app_js
    assert "今日 ${formatStatNumber(item.daily_total)}" in app_js
