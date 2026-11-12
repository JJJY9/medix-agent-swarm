"""PWA 离线恢复相关前端逻辑的静态与语法测试。"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def test_service_worker_handles_notification_click():
    text = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
    assert "addEventListener('notificationclick'" in text
    assert "?action=reminders" in text
    assert "clients.openWindow" in text
    assert "includeUncontrolled: true" in text


def test_app_retries_failed_service_worker_install():
    text = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for marker in (
        "scheduleServiceWorkerRetry",
        "worker.state === 'redundant'",
        "!registration.active",
        "swRegistration.update()",
        "swRegistration.installing",
    ):
        assert marker in text


def test_app_retries_initial_loads_after_reconnect():
    text = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for marker in (
        "statusWasDown = true",
        "retryAfterReconnect",
        "await initProfiles()",
        "await refreshReminderBadge()",
        "restoreCurrentSession()",
    ):
        assert marker in text


def test_fallback_notification_opens_reminders():
    text = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "fallback.onclick" in text
    assert "openReminders()" in text


@pytest.mark.parametrize("name", ["web/app.js", "web/sw.js"])
def test_web_scripts_parse(name):
    if not NODE:
        pytest.skip("node 不可用，跳过 JS 语法检查")
    result = subprocess.run(
        [NODE, "--check", str(ROOT / name)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
