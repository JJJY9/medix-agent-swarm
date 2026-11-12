"""自动建提醒的原子查重。"""
import threading

from memory.session_store import SessionStore


def test_concurrent_ensure_creates_one_reminder(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    barrier = threading.Barrier(4)
    results = []

    def worker():
        barrier.wait()
        results.append(store.ensure_reminder(
            "补货：药A",
            "2026-12-01",
            profile="妈妈",
            medication_id=1,
        ))

    try:
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

        reminders = store.list_reminders(True, "妈妈")
    finally:
        store.close()

    assert len(results) == 4
    assert sum(1 for _reminder, created in results if created) == 1
    assert len({reminder["id"] for reminder, _ in results}) == 1
    assert len(reminders) == 1


def test_ensure_reminder_matches_linked_record_without_title(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first, created = store.ensure_reminder(
            "接种：流感（第1剂）",
            "2026-12-01",
            profile="妈妈",
            vaccination_id=7,
            match_title=False,
        )
        second, created_again = store.ensure_reminder(
            "接种：流感疫苗（第1剂）",
            "2026-12-01",
            profile="妈妈",
            vaccination_id=7,
            match_title=False,
        )
    finally:
        store.close()

    assert created is True
    assert created_again is False
    assert second["id"] == first["id"]


def test_ensure_reminder_can_match_due_date(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first, _ = store.ensure_reminder(
            "复查：血压", "2026-12-01", profile="妈妈", match_due=True,
        )
        same, same_created = store.ensure_reminder(
            "复查：血压", "2026-12-01", profile="妈妈", match_due=True,
        )
        moved, moved_created = store.ensure_reminder(
            "复查：血压", "2026-12-08", profile="妈妈", match_due=True,
        )
    finally:
        store.close()

    assert same["id"] == first["id"]
    assert same_created is False
    assert moved["id"] != first["id"]
    assert moved_created is True
