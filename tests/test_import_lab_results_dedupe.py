"""备份恢复保留同一检测时间的多条化验结果。"""
from memory.session_store import SessionStore


def test_backup_import_keeps_same_time_lab_results(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.add_lab_result(
            "妈妈", "ALT", 55, "U/L", 7, 40,
            tested_at="2026-01-02",
        )
        source.add_lab_result(
            "妈妈", "ALT", 60, "U/L", 7, 40,
            tested_at="2026-01-02",
        )
        data = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        first = target.import_data(data)
        rows = target.list_lab_results(profile="妈妈")
        second = target.import_data(data)
    finally:
        target.close()

    assert first["lab_results"] == 2
    assert {round(row["value"]) for row in rows} == {55, 60}
    assert second["lab_results"] == 0
