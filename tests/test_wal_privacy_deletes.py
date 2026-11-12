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
