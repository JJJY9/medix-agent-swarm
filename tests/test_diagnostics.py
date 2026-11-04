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
    store.close()

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
    assert runtime["auth_enabled"] is True
    assert runtime["rate_limit"] == 5
    assert runtime["pricing_configured"] is True
    assert runtime["monthly_token_budget"] == 100000
    assert runtime["auto_archive_days"] == 14
    assert "secret-token" not in str(runtime)


def test_missing_required_is_consistent():
    report = collect_report()
    expected = {
        package["package"]
        for package in report["packages"]
        if package["required"] and not package["ok"]
    }
    assert set(report["missing_required"]) == expected
