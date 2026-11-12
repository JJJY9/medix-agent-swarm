"""CSV 导入保留不同疗程与不同创建时间的记录。"""
from memory.session_store import SessionStore


def test_medication_csv_keeps_separate_courses(tmp_path):
    payload = {
        "medications": [
            {
                "profile": "妈妈",
                "name": "氨氯地平",
                "dose": "5 mg",
                "frequency": "每日一次",
                "started_at": "2024-01-01",
                "stopped_at": "2024-06-01",
                "stop_reason": "调整方案",
                "active": False,
            },
            {
                "profile": "妈妈",
                "name": "氨氯地平",
                "dose": "5 mg",
                "frequency": "每日一次",
                "started_at": "2025-01-01",
                "active": True,
            },
        ]
    }
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first = store.import_data(payload)
        rows = store.list_medications(profile="妈妈")
        second = store.import_data(payload)
    finally:
        store.close()

    assert first["medications"] == 2
    assert len(rows) == 2
    assert {row["active"] for row in rows} == {0, 1}
    assert second["medications"] == 0


def test_symptoms_with_same_content_but_different_created_at_are_kept(
    tmp_path,
):
    payload = {
        "symptoms": [
            {
                "profile": "妈妈",
                "symptom": "头痛",
                "severity": 5,
                "recorded_at": "2026-11-01T08:00",
                "note": "晨起",
                "created_at": "2026-11-01T08:01:00",
            },
            {
                "profile": "妈妈",
                "symptom": "头痛",
                "severity": 5,
                "recorded_at": "2026-11-01T08:00",
                "note": "晨起",
                "created_at": "2026-11-02T09:00:00",
            },
        ]
    }
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first = store.import_data(payload)
        rows = store.list_symptoms(profile="妈妈")
        second = store.import_data(payload)
    finally:
        store.close()

    assert first["symptoms"] == 2
    assert len(rows) == 2
    assert second["symptoms"] == 0


def test_metric_restore_keeps_same_minute_readings(tmp_path):
    payload = {
        "health_metrics": [
            {
                "profile": "妈妈",
                "metric": "weight",
                "value1": 60,
                "unit": "kg",
                "measured_at": "2026-11-01T08:00",
                "context": "",
                "note": "",
                "created_at": "2026-11-01T08:00:10",
            },
            {
                "profile": "妈妈",
                "metric": "weight",
                "value1": 60,
                "unit": "kg",
                "measured_at": "2026-11-01T08:00",
                "context": "",
                "note": "",
                "created_at": "2026-11-01T08:00:40",
            },
        ]
    }
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first = store.import_data(payload)
        rows = store.list_metrics("妈妈")
        second = store.import_data(payload)
    finally:
        store.close()

    # 同一分钟的两条同值读数都是合法记录，恢复时不能合并
    assert first["metrics"] == 2
    assert len(rows) == 2
    assert {row["created_at"] for row in rows} == {
        "2026-11-01T08:00:10",
        "2026-11-01T08:00:40",
    }
    # 再次导入同一份备份仍然幂等
    assert second["metrics"] == 0
