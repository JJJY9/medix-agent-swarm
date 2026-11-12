"""备份恢复：同名同剂量但不同疗程的用药必须保留为两条。"""
from memory.session_store import SessionStore


def test_backup_import_keeps_separate_medication_courses(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        first = source.add_medication(
            "", "氨氯地平", dose="5 mg", frequency="每日一次",
            started_at="2025-06-01",
        )
        assert first
        source.update_medication(
            first["id"], "", "氨氯地平", dose="5 mg", frequency="每日一次",
            active=False, started_at="2025-06-01", stopped_at="2026-01-31",
            stop_reason="换药",
        )
        second = source.add_medication(
            "", "氨氯地平", dose="5 mg", frequency="每日一次",
            started_at="2026-06-01",
        )
        assert second
        source.add_medication_log(first["id"], taken_at="2026-01-15T08:00")
        source.add_medication_log(second["id"], taken_at="2026-06-10T08:00")
        data = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        result = target.import_data(data)
        meds = [
            row for row in target.list_medications()
            if row["name"] == "氨氯地平"
        ]
        logs = target.list_medication_logs(limit=50)
        again = target.import_data(data)
    finally:
        target.close()

    assert result["medications"] == 2
    assert len(meds) == 2
    by_active = {bool(row["active"]): row for row in meds}
    assert by_active[False]["stopped_at"] == "2026-01-31"
    assert by_active[False]["stop_reason"] == "换药"
    assert by_active[True]["started_at"] == "2026-06-01"
    assert {log["medication_id"] for log in logs} == {
        row["id"] for row in meds
    }
    assert again["medications"] == 0
    assert again["medication_logs"] == 0
