"""VSCode 配置在 Windows 与 macOS/Linux 上都可用。"""
import json
from pathlib import Path

VSCODE = Path(__file__).resolve().parent.parent / ".vscode"


def _load(name):
    return json.loads((VSCODE / name).read_text(encoding="utf-8"))


def test_launch_configs_use_selected_interpreter():
    launch = _load("launch.json")
    assert launch["configurations"]
    for config in launch["configurations"]:
        assert "python" not in config


def test_tasks_use_selected_interpreter():
    tasks = _load("tasks.json")
    assert tasks["tasks"]
    for task in tasks["tasks"]:
        assert "${command:python.interpreterPath}" in task["command"]
        assert ".venv/Scripts" not in task["command"]


def test_settings_do_not_hardcode_windows_interpreter():
    settings = _load("settings.json")
    assert "python.defaultInterpreterPath" not in settings
