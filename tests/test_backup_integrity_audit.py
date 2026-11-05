"""备份恢复的上限与重复提醒回归测试。"""
import pytest

import memory.session_store as store_module
from memory.session_store import SessionStore


def test_import_rejects_session_over_message_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "MAX_BACKUP_SESSION_MESSAGES", 2)
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        payload = {
            "sessions": [{
                "id": "too-many",
                "messages": [
                    {"role": "user", "content": "一"},
                    {"role": "assistant", "content": "二"},
                    {"role": "user", "content": "三"},
                ],
            }],
        }
        with pytest.raises(ValueError, match="最多导入"):
            store.import_data(payload)
        assert store.get_session("too-many") is None
    finally:
        store.close()


def test_import_preserves_completed_repeat_parent(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        parent = source.add_reminder(
            "用药：氨氯地平", "2026-11-09T09:00", repeat_days=1
        )
        child = source.complete_reminder_with_repeat(parent["id"])
        assert child is not None

        payload = source.export_data()
        target.import_data(payload)

        reminders = target.list_reminders(include_done=True)
        assert len(reminders) == 2
        completed = next(item for item in reminders if item["done"])
        pending = next(item for item in reminders if not item["done"])
        assert completed["title"] == "用药：氨氯地平"
        assert pending["title"] == "用药：氨氯地平"
        assert pending["repeats_from"] == completed["id"]
    finally:
        source.close()
        target.close()
