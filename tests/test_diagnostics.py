"""环境自检模块的单元测试。"""
import sys

from core.diagnostics import MIN_PYTHON, check_packages, collect_report


def test_report_shape():
    report = collect_report()

    assert report["python_ok"] is (sys.version_info >= MIN_PYTHON)
    assert {"os", "python", "packages", "missing_required", "llm_config", "knowledge"}.issubset(report)
    assert "api_key" not in report["llm_config"], "自检结果不能包含 API Key 本身"
    assert isinstance(report["knowledge"]["documents"], int)
    assert isinstance(report["knowledge"]["uploads"], int)


def test_packages_include_required_packages():
    packages = check_packages()
    names = {package["package"] for package in packages}

    assert {"openai", "fastapi", "sentence-transformers", "pyyaml"}.issubset(names)
    assert all("required" in package and "ok" in package for package in packages)


def test_missing_required_is_consistent():
    report = collect_report()
    expected = {
        package["package"]
        for package in report["packages"]
        if package["required"] and not package["ok"]
    }
    assert set(report["missing_required"]) == expected
