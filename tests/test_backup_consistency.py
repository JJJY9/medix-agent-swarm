"""备份一致性与审计日志顺序的回归测试。"""
import threading
import time

from memory.session_store import SessionStore


def test_audit_log_order_survives_export_import(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        with source._lock, source._conn:
            source._conn.executemany(
                "INSERT INTO audit_log(action, target, detail, created_at)"
                " VALUES (?, ?, ?, ?)",
                [
                    ("one", "", "", "2026-01-01T10:00:00"),
                    ("two", "", "", "2026-01-01T10:01:00"),
                    ("three", "", "", "2026-01-01T10:02:00"),
                ],
            )

        payload = source.export_data()
        target.import_data(payload)

        actions = [
            item["action"] for item in target.list_audit_log(limit=10)
        ]
        assert actions == ["three", "two", "one"]
    finally:
        source.close()
        target.close()


def test_export_data_holds_lock_while_reading_later_tables(tmp_path):
    store = SessionStore(str(tmp_path / "lock.db"))
    profiles_read = threading.Event()
    allow_continue = threading.Event()
    write_started = threading.Event()
    write_done = threading.Event()
    errors = []

    def blocked_list_profiles():
        profiles_read.set()
        allow_continue.wait(2)
        return []

    store.list_profiles = blocked_list_profiles

    def export():
        try:
            store.export_data()
        except Exception as e:  # pragma: no cover - 断言失败时给出原因
            errors.append(e)

    def write():
        write_started.set()
        try:
            store.log_action("test.action")
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            write_done.set()

    exporter = threading.Thread(target=export)
    writer = threading.Thread(target=write)
    try:
        exporter.start()
        assert profiles_read.wait(2)
        writer.start()
        assert write_started.wait(2)
        time.sleep(0.1)
        # 导出持有锁期间，写入必须等待，避免跨表快照不一致
        assert not write_done.is_set()
        allow_continue.set()
        exporter.join(2)
        writer.join(2)
        assert exporter.is_alive() is False
        assert writer.is_alive() is False
        assert write_done.is_set()
        assert not errors
    finally:
        allow_continue.set()
        exporter.join(1)
        writer.join(1)
        store.close()
