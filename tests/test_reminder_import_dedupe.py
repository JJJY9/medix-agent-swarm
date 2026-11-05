"""提醒导入去重的回归测试：判重不受导入时刻影响。"""
from memory.session_store import SessionStore


def test_csv_reminder_import_dedupe_survives_time_gap(
    tmp_path, monkeypatch
):
    store = SessionStore(str(tmp_path / "dedupe.db"))
    try:
        payload = {
            "sessions": [],
            "reminders": [{
                "title": "复查",
                "due_at": "2026-11-10",
                "note": "空腹",
            }],
        }

        first = store.import_data(payload)
        # 模拟两次导入相隔很久（created_at 会不同）
        monkeypatch.setattr(store, "_now", lambda: "2099-01-01T00:00:00")
        second = store.import_data(payload)

        assert first["reminders"] == 1
        assert second["reminders"] == 0
        assert len(store.list_reminders()) == 1
    finally:
        store.close()
