"""最新指标查询的等价性与并列时间取舍。"""
from datetime import datetime, timedelta

from memory.session_store import SessionStore


def _at(days_ago, hour=8):
    return (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    ).isoformat(timespec="minutes")


def test_latest_metrics_by_profile_picks_latest_then_highest_id(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.add_metric("妈妈", "weight", 60, measured_at=_at(5))
        older = store.add_metric("妈妈", "weight", 61, measured_at=_at(2))
        newer = store.add_metric("妈妈", "weight", 62, measured_at=_at(2))
        bp = store.add_metric(
            "妈妈", "bp", 130, 85, measured_at=_at(1), context="morning"
        )
        dad = store.add_metric("爸爸", "weight", 75, measured_at=_at(1))

        rows = store.latest_metrics_by_profile()
    finally:
        store.close()

    assert newer["id"] > older["id"]
    by_key = {(row["profile"], row["metric"]): row for row in rows}
    assert set(by_key) == {("妈妈", "weight"), ("妈妈", "bp"), ("爸爸", "weight")}
    assert by_key[("妈妈", "weight")]["id"] == newer["id"]
    assert by_key[("妈妈", "weight")]["value1"] == 62
    assert by_key[("妈妈", "bp")]["id"] == bp["id"]
    assert by_key[("爸爸", "weight")]["id"] == dad["id"]
    assert [row["profile"] for row in rows] == sorted(
        row["profile"] for row in rows
    )


def test_latest_metrics_filters_by_profile_and_picks_highest_id(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.add_metric("妈妈", "weight", 60, measured_at=_at(4))
        older = store.add_metric("妈妈", "weight", 61, measured_at=_at(3))
        newer = store.add_metric("妈妈", "weight", 62, measured_at=_at(3))
        store.add_metric("妈妈", "glucose", 5.6, measured_at=_at(2))
        store.add_metric("爸爸", "weight", 80, measured_at=_at(1))

        rows = store.latest_metrics(profile="妈妈")
    finally:
        store.close()

    assert newer["id"] > older["id"]
    by_metric = {row["metric"]: row for row in rows}
    assert set(by_metric) == {"weight", "glucose"}
    assert by_metric["weight"]["id"] == newer["id"]
    assert by_metric["glucose"]["value1"] == 5.6
    assert all(row["profile"] == "妈妈" for row in rows)


def test_latest_metrics_respects_days_window(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        old = store.add_metric("妈妈", "weight", 55, measured_at=_at(40))
        recent = store.add_metric("妈妈", "weight", 60, measured_at=_at(2))

        rows = store.latest_metrics(profile="妈妈", days=7)
    finally:
        store.close()

    assert recent["id"] != old["id"]
    assert len(rows) == 1
    assert rows[0]["value1"] == 60
