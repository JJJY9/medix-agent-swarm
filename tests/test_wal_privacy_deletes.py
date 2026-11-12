"""删除健康数据后，明文不能残留在 WAL 文件中。"""
from pathlib import Path

from memory.session_store import SessionStore

MARKER = "敏感标记XYZ"


def _assert_absent(db_path, marker=MARKER):
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        if path.exists():
            assert marker.encode("utf-8") not in path.read_bytes(), suffix


def _open(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


def test_delete_allergy_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        row = store.add_allergy("妈妈", MARKER, "皮疹")
        assert store.delete_allergy(row["id"])
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_delete_lab_result_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        row = store.add_lab_result("妈妈", MARKER, 10, "U/L")
        assert store.delete_lab_result(row["id"])
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_delete_medication_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        row = store.add_medication("妈妈", MARKER)
        assert store.delete_medication(row["id"])
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_delete_visit_record_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        row = store.add_visit_record("妈妈", diagnosis=MARKER)
        assert store.delete_visit_record(row["id"])
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_delete_symptom_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        row = store.add_symptom("妈妈", MARKER, 5)
        assert store.delete_symptom(row["id"])
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_delete_profile_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        assert store.save_profile(MARKER, {"age": "40"})
        assert store.delete_profile(MARKER)
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_delete_metric_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        row = store.add_metric("妈妈", "weight", 60, note=MARKER)
        assert store.delete_metric(row["id"])
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_delete_reminder_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        row = store.add_reminder(MARKER, "2099-01-01", note=MARKER)
        assert store.delete_reminder(row["id"])
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_delete_attachment_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        row = store.add_attachment(
            "妈妈", "other", 0, f"{MARKER}.pdf", "stored.pdf",
            "application/pdf", 100, note=MARKER,
        )
        assert store.delete_attachment(row["id"]) is not None
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_delete_metric_target_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        assert store.set_metric_target(MARKER, "weight", 60)
        assert store.delete_metric_target(MARKER, "weight")
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()


def test_redact_audit_clears_wal(tmp_path):
    store = _open(tmp_path)
    try:
        store.log_action("reminder.delete", MARKER, MARKER)
        changed = store.redact_audit_targets(
            ["reminder.delete"], MARKER, "已删除", detail="已清理"
        )
        assert changed == 1
        _assert_absent(tmp_path / "sessions.db")
    finally:
        store.close()
