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
