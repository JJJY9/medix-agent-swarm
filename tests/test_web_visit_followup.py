"""就诊复诊一键安排与复查自动提醒的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def test_visit_followup_button_is_rendered():
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "scheduleVisitFollowup" in script
    assert "/schedule-followup" in script
    assert "安排复诊" in script
    assert "已安排复诊" in script
    assert "followup_scheduled" in script


def test_care_done_auto_reminder_feedback():
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "reminder_created" in script
    assert "已创建提前 7 天提醒" in script
    assert "refreshReminderBadge" in script
