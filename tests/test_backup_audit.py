"""备份完整性、提醒导入主键冲突与 Lead 解析的回归测试。"""
from memory.session_store import SessionStore
from swarm.lead_agent import extract_json_payload


def test_export_and_import_include_audit_log(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        source.log_action("session.export", "s1", "detail")
        data = source.export_data()

        assert data["audit_log"][0]["action"] == "session.export"

        first = target.import_data(data)
        assert first["audit_log"] == 1
        assert target.list_audit_log()[0]["action"] == "session.export"

        again = target.import_data(data)
        assert again["audit_log"] == 0
    finally:
        source.close()
        target.close()


def test_import_preserves_duplicate_audit_events(tmp_path):
    """同一分钟的相同审计事件应全部恢复，重复导入仍保持幂等。"""
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        row = {
            "action": "medication.log",
            "target": "1·2026-11-05T04:49",
            "detail": "",
            "created_at": "2026-11-05T04:49:10",
        }
        data = {"sessions": [], "audit_log": [row, row, row]}

        first = target.import_data(data)
        assert first["audit_log"] == 3
        assert len(target.list_audit_log(limit=10)) == 3

        again = target.import_data(data)
        assert again["audit_log"] == 0
        assert len(target.list_audit_log(limit=10)) == 3
    finally:
        target.close()


def test_import_links_legacy_medication_reminder(tmp_path):
    """旧备份没有 medication_id，导入后应按「用药：药名」恢复联动。"""
    store = SessionStore(str(tmp_path / "legacy.db"))
    try:
        result = store.import_data({
            "sessions": [],
            "medications": [{
                "name": "氨氯地平", "profile": "妈妈", "dose": "5 mg",
            }],
            "reminders": [{
                "title": "用药：氨氯地平", "profile": "妈妈",
                "due_at": "2026-11-20",
            }],
        })

        assert result["medications"] == 1
        assert result["reminders"] == 1
        reminders = store.list_reminders(True, "妈妈")
        assert reminders[0]["medication_id"] > 0
        medications = store.list_medications("妈妈", False, 10)
        assert reminders[0]["medication_id"] == medications[0]["id"]
    finally:
        store.close()


def test_startup_backfills_reminder_medication_link(tmp_path):
    """升级中断或旧库恢复留下的 medication_id=0 提醒，重启时应补关联。"""
    db_path = str(tmp_path / "upgrade.db")
    store = SessionStore(db_path)
    try:
        medication = store.add_medication("妈妈", "氨氯地平")
        reminder = store.add_reminder(
            "用药：氨氯地平", "2026-11-20", profile="妈妈"
        )
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE reminders SET medication_id = 0 WHERE id = ?",
                (reminder["id"],),
            )
        medication_id = medication["id"]
    finally:
        store.close()

    reopened = SessionStore(db_path)
    try:
        reminders = reopened.list_reminders(True, "妈妈")
        assert reminders[0]["medication_id"] == medication_id
    finally:
        reopened.close()


def test_reminder_id_collision_keeps_both_records(tmp_path):
    store = SessionStore(str(tmp_path / "reminders.db"))
    try:
        local = store.add_reminder("吃降压药", "2026-11-10")
        payload = {
            "sessions": [],
            "reminders": [{
                "id": local["id"],
                "title": "复查血压",
                "due_at": "2026-11-12",
            }],
        }

        result = store.import_data(payload)

        assert result["reminders"] == 1
        titles = sorted(item["title"] for item in store.list_reminders())
        assert titles == ["吃降压药", "复查血压"]
    finally:
        store.close()


def test_lead_parser_skips_irrelevant_array():
    content = (
        "结论如下：\n"
        '[{"note": "参考"}]\n'
        '{"subtasks": [{"description": "评估胸痛风险", '
        '"assigned_agent": "diagnostic_agent"}]}'
    )

    payload = extract_json_payload(content)

    assert payload["subtasks"][0]["description"] == "评估胸痛风险"


def test_lead_parser_returns_none_for_plain_arrays():
    assert extract_json_payload('[{"note": "参考"}]') is None
