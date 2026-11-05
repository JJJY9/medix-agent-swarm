"""健康统计窗口、重复提醒与档案管理的回归测试。"""
from datetime import datetime, timedelta

import pytest

from memory.session_store import SessionStore, _next_repeat_due


@pytest.fixture
def store(tmp_path):
    instance = SessionStore(str(tmp_path / "health.db"))
    yield instance
    instance.close()


def test_metric_summary_includes_midnight_record(store):
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_metric("", "weight", 70, measured_at=today)

    summary = store.metric_summary(metric="weight", days=1)

    assert summary["count"] == 1
    assert summary["value1"]["prev_avg"] is None


def test_next_repeat_due_keeps_today():
    today = datetime.now().date()
    yesterday = (today - timedelta(days=1)).isoformat()

    assert _next_repeat_due(yesterday, 1) == today.isoformat()


def test_next_repeat_due_lands_on_weekly_grid():
    today = datetime.now().date()
    five_days_ago = (today - timedelta(days=5)).isoformat()

    # 上一周期在两天后，下一次应在 +2 天而不是跳过一整个周期
    assert _next_repeat_due(five_days_ago, 7) == (
        today + timedelta(days=2)
    ).isoformat()


def test_rename_data_only_profile(store):
    store.add_metric("妈妈", "weight", 60)

    renamed = store.rename_profile("妈妈", "母亲")

    assert renamed == "母亲"
    assert [item["profile"] for item in store.list_metrics("母亲")] == ["母亲"]


def test_rename_rejects_name_with_existing_data(store):
    store.save_profile("父亲", {"age": "60"})
    store.add_metric("妈妈", "weight", 60)

    with pytest.raises(ValueError):
        store.rename_profile("父亲", "妈妈")


def test_delete_profile_clears_metric_targets(store):
    store.save_profile("妈妈", {"age": "58"})
    store.set_metric_target("妈妈", "weight", 70)

    assert store.delete_profile("妈妈") is True
    store.save_profile("妈妈", {"age": "30"})

    assert store.list_metric_targets("妈妈") == []


def test_reopen_reminder_with_self_reference_survives(store):
    created = store.add_reminder("复查", "2026-11-10")
    with store._conn:
        store._conn.execute(
            "UPDATE reminders SET repeats_from = id WHERE id = ?",
            (created["id"],),
        )
    store.complete_reminder_with_repeat(created["id"])

    assert store.reopen_reminder(created["id"]) is True
    reminders = store.list_reminders(include_done=True)
    assert any(item["id"] == created["id"] for item in reminders)


def test_repeat_until_time_is_respected(store):
    reminder = store.add_reminder(
        "服药", "2026-11-09T09:00",
        repeat_days=1, repeat_until="2026-11-10T08:00",
    )

    result = store.complete_reminder_with_repeat(reminder["id"])

    assert result["completed"] is True
    assert result["next"] is None


def test_repeat_until_date_allows_same_day(store):
    reminder = store.add_reminder(
        "服药", "2026-11-09T09:00",
        repeat_days=1, repeat_until="2026-11-10",
    )

    result = store.complete_reminder_with_repeat(reminder["id"])

    assert result["next"] is not None
    assert result["next"]["due_at"].startswith("2026-11-10")
