"""环境自检模块的单元测试。"""
import sys

from core.diagnostics import (
    MIN_PYTHON,
    check_packages,
    check_runtime,
    collect_report,
)
from memory.session_store import SessionStore


def test_report_shape():
    report = collect_report()

    assert report["python_ok"] is (sys.version_info >= MIN_PYTHON)
    assert {
        "os", "python", "packages", "missing_required",
        "llm_config", "knowledge", "runtime",
    }.issubset(report)
    assert "api_key" not in report["llm_config"], "自检结果不能包含 API Key 本身"
    assert isinstance(report["knowledge"]["documents"], int)
    assert isinstance(report["knowledge"]["uploads"], int)
    assert "sessions" in report["runtime"]


def test_packages_include_required_packages():
    packages = check_packages()
    names = {package["package"] for package in packages}

    assert {"openai", "fastapi", "sentence-transformers", "pyyaml"}.issubset(names)
    assert all("required" in package and "ok" in package for package in packages)


def test_runtime_reports_database_and_flags(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_SESSION_DB", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = SessionStore(str(data_dir / "sessions.db"))
    store.save_exchange("s1", "测试问题", "测试答复")
    store.save_profile("妈妈", {"age": "58"})
    store.add_metric(
        "妈妈", "weight", 63, measured_at="2026-11-05T08:00"
    )
    store.set_metric_target("妈妈", "weight", 65)
    store.add_symptom("妈妈", "头晕", 4, "2026-11-05T08:00")
    medication = store.add_medication("妈妈", "氨氯地平", "5 mg")
    store.add_medication_log(medication["id"], "2026-11-05T08:00")
    store.add_reminder("复查", "2026-11-10", profile="妈妈")
    store.close()

    attachment_dir = data_dir / "attachments"
    attachment_dir.mkdir()
    (attachment_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (attachment_dir / "b.pdf").write_bytes(b"%PDF-1.4 bb")

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("MEDIX_RATE_LIMIT", "5")
    monkeypatch.setenv("MEDIX_PRICE_INPUT", "2")
    monkeypatch.setenv("MEDIX_PRICE_OUTPUT", "8")
    monkeypatch.setenv("MEDIX_MONTHLY_TOKEN_BUDGET", "100000")
    monkeypatch.setenv("MEDIX_AUTO_ARCHIVE_DAYS", "14")

    runtime = check_runtime(tmp_path)

    assert runtime["db_exists"] is True
    assert runtime["db_size"] > 0
    assert runtime["sessions"] == 1
    assert runtime["messages"] == 2
    assert runtime["profiles"] == 1
    assert runtime["metrics"] == 1
    assert runtime["targets"] == 1
    assert runtime["symptoms"] == 1
    assert runtime["medications"] == 1
    assert runtime["medication_logs"] == 1
    assert runtime["reminders"] == 1
    assert runtime["auth_enabled"] is True
    assert runtime["rate_limit"] == 5
    assert runtime["pricing_configured"] is True
    assert runtime["monthly_token_budget"] == 100000
    assert runtime["auto_archive_days"] == 14
    assert runtime["attachment_dir_exists"] is True
    assert runtime["attachment_count"] == 2
    assert runtime["attachment_size"] == (
        len(b"%PDF-1.4 a") + len(b"%PDF-1.4 bb")
    )
    assert "secret-token" not in str(runtime)


def test_runtime_marks_unreadable_tables(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.delenv("MEDIX_SESSION_DB", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE sessions (id TEXT)")
        conn.execute("CREATE TABLE messages (id INTEGER)")
        conn.commit()
    finally:
        conn.close()

    runtime = check_runtime(tmp_path)

    assert runtime["sessions"] == 0
    assert runtime["messages"] == 0
    assert runtime["metrics"] is None
    assert "health_metrics" in runtime["data_errors"]
    assert "medication_logs" in runtime["data_errors"]
    assert runtime["attachment_dir_exists"] is False
    assert runtime["attachment_count"] == 0
    assert runtime["attachment_size"] == 0


def test_missing_required_is_consistent():
    report = collect_report()
    expected = {
        package["package"]
        for package in report["packages"]
        if package["required"] and not package["ok"]
    }
    assert set(report["missing_required"]) == expected
