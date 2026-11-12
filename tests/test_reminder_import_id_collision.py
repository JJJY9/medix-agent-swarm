"""提醒导入：原始 ID 撞号不能当成同一条提醒跳过。"""
from memory.session_store import SessionStore


def _insert_reminder_with_id(store, reminder_id, title, profile, due_at,
                             created_at):
    with store._lock, store._conn:
        store._conn.execute(
            """
            INSERT INTO reminders(
                id, title, note, due_at, session_id, profile,
                advance_days, repeat_days, repeat_until, repeats_from,
                medication_id, vaccination_id, appointment_id, done,
                created_at, updated_at
            ) VALUES (?, ?, '', ?, '', ?, 0, 0, '', 0, 0, 0, 0, 0, ?, ?)
            """,
            (reminder_id, title, due_at, profile, created_at, created_at),
        )


def test_id_collision_with_different_reminder_is_not_skipped(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        _insert_reminder_with_id(
            store, 5, "复查血压", "妈妈",
            "2020-01-02", "2020-01-01T00:00:00",
        )
        payload = {
            "reminders": [{
                "id": 5,
                "title": "复查血压",
                "profile": "妈妈",
                "due_at": "2026-12-05",
                "created_at": "2026-11-01T10:00:00",
            }]
        }

        first = store.import_data(payload)
        rows = store.list_reminders(True, "妈妈")
        second = store.import_data(payload)
    finally:
        store.close()

    assert first["reminders"] == 1
    assert len(rows) == 2
    assert second["reminders"] == 0


def test_id_collision_with_same_created_at_is_treated_as_imported(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        _insert_reminder_with_id(
            store, 5, "复查血压", "妈妈",
            "2026-12-05", "2026-11-01T10:00:00",
        )
        payload = {
            "reminders": [{
                "id": 5,
                "title": "复查血压",
                "profile": "妈妈",
                "due_at": "2026-12-05",
                "created_at": "2026-11-01T10:00:00",
            }]
        }

        result = store.import_data(payload)
        rows = store.list_reminders(True, "妈妈")
    finally:
        store.close()

    assert result["reminders"] == 0
    assert len(rows) == 1
