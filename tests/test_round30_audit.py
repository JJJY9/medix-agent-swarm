"""第三十轮审查：墓碑/导入/化验/过敏/档案/会话/PWA 回归测试。"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient(api_server.app), session_store
    finally:
        session_store.close()


@pytest.fixture
def store(tmp_path):
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        yield session_store
    finally:
        session_store.close()


def _days_ago(days: int, hour: int = 8) -> str:
    return (datetime.now() - timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    ).isoformat(timespec="minutes")


def test_batch_delete_tombstone_survives_reopen(tmp_path):
    from memory.session_store import SessionStore

    db_path = str(tmp_path / "batch.db")
    first = SessionStore(db_path)
    try:
        first.save_exchange("s1", "问题一", "回答一")
        first.save_exchange("s2", "问题二", "回答二")
        result = first.delete_sessions_many(["s1", "s2"])
        assert result["deleted"] == ["s1", "s2"]
    finally:
        first.close()

    second = SessionStore(db_path)
    try:
        assert second.is_session_deleted("s1") is True
        assert second.save_exchange("s1", "新问题", "新回答") is None
    finally:
        second.close()


def test_purge_sessions_tombstone_survives_reopen(tmp_path):
    from memory.session_store import SessionStore

    db_path = str(tmp_path / "purge.db")
    first = SessionStore(db_path)
    try:
        first.save_exchange("old", "旧问题", "旧回答")
        result = first.purge_sessions_before(
            (datetime.now() + timedelta(days=1)).isoformat(
                timespec="seconds"
            )
        )
        assert result["sessions"] == 1
    finally:
        first.close()

    second = SessionStore(db_path)
    try:
        assert second.is_session_deleted("old") is True
    finally:
        second.close()


def test_reminder_restore_keeps_same_second_rows(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        records = [
            {
                "profile": "妈妈", "title": f"复查{index}",
                "due_at": "2026-11-20", "created_at": "2026-11-01T09:00:00",
            }
            for index in (1, 2, 3)
        ]
        source.import_data({"reminders": records})
        backup = source.export_data()

        target.add_reminder("本地提醒", "2026-11-10", profile="妈妈")
        result = target.import_data(backup)

        assert result["reminders"] == 3
        titles = sorted(
            row["title"] for row in target.list_reminders(True, "妈妈")
        )
        assert titles == ["复查1", "复查2", "复查3", "本地提醒"]
    finally:
        source.close()
        target.close()


def test_count_units_wan_and_qian():
    from core.lab_values import lab_critical_alerts

    assert lab_critical_alerts("血小板", 15, "万/μL") == []
    assert lab_critical_alerts("白细胞", 6.5, "千/μL") == []
    assert lab_critical_alerts("血小板", 1, "万/μL")
    assert lab_critical_alerts("白细胞", 6.5, "K/μL") == []


def test_inr_chinese_name_and_unitless_one():
    from core.lab_values import lab_critical_alerts

    assert lab_critical_alerts("国际标准化比率", 6.0, "1")
    assert lab_critical_alerts("INR", 6.0, "")
    assert lab_critical_alerts("国际标准化比值", 2.0, "1") == []


def test_mixed_panel_does_not_hide_blood_critical():
    from core.lab_values import lab_critical_alerts

    assert lab_critical_alerts("钾", 7.0, "mmol/L", panel="血常规+尿常规")
    assert lab_critical_alerts("钾", 7.0, "mmol/L", panel="肾功能")
    assert lab_critical_alerts("钾", 40, "mmol/L", panel="尿电解质") == []


def test_nsaid_class_allergy_triggers_cross_allergy():
    from core.medication_safety import check_medication_safety

    result = check_medication_safety(
        "我对非甾体抗炎药过敏，能吃布洛芬吗",
        {"allergies": "非甾体抗炎药"},
    )
    titles = [alert["title"] for alert in result.get("alerts", [])]
    assert any("非甾体抗炎药" in title for title in titles)


def test_allergy_negative_result_is_ignored():
    from core.medication_safety import (
        _allergy_mentions_in_question,
        check_medication_safety,
    )

    assert _allergy_mentions_in_question("青霉素过敏史阴性") == ""
    assert _allergy_mentions_in_question("青霉素过敏原阴性") == ""
    result = check_medication_safety(
        "青霉素过敏史阴性，能吃阿莫西林吗", {}
    )
    assert result.get("alerts") == []


def test_storage_owner_marker_protects_custom_attachments(tmp_path):
    from core.storage_paths import attachment_dir_for_db

    custom_db = tmp_path / "family.db"
    legacy = tmp_path / "attachments"
    legacy.mkdir()
    (legacy / "abc.pdf").write_bytes(b"x")
    custom_dir = attachment_dir_for_db(custom_db)
    assert custom_dir == legacy
    assert (tmp_path / ".medix-attachments-owner").read_text(
        encoding="utf-8"
    ) == "family.db"
    # 真实使用中 SessionStore 会先创建数据库文件；标记指向的文件
    # 不存在会被视为过期标记（数据库被改名/移走）
    custom_db.write_bytes(b"db")

    # 默认库后来出现：不能接管已认领的目录
    default_dir = attachment_dir_for_db(tmp_path / "sessions.db")
    assert default_dir != legacy


def test_deleted_profile_not_rebound_after_request(tmp_path):
    import asyncio

    import api_server
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "rebind.db"))
    try:
        session_store.save_profile("妈妈", {"sex": "女"})
        session_store.ensure_session("s1", title="历史会话")
        session_store.delete_profile("妈妈")

        asyncio.run(api_server._link_patient_profile("s1", "妈妈"))

        session = session_store.get_session("s1")
        assert session["profile"] == ""
    finally:
        session_store.close()


def test_deleted_session_messages_return_404(client_and_store):
    client, store = client_and_store
    store.save_exchange("s1", "问题", "回答")
    store.delete_session("s1")

    response = client.get("/api/sessions/s1/messages")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_deleted"


def test_backup_inspect_warns_about_attachments(
    client_and_store, tmp_path, monkeypatch
):
    client, _store = client_and_store
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setenv("MEDIX_BACKUP_DIR", str(backup_dir))
    payload = {
        "version": 1,
        "sessions": [{
            "id": "s1",
            "messages": [{"role": "user", "content": "问题"}],
        }],
    }
    name = "medix-backup-20261113-130000.json"
    (backup_dir / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    data = client.get(f"/api/backups/{name}/inspect").json()

    assert any("不包含附件" in warning for warning in data["warnings"])


def test_frequency_weekly_count_not_weekday():
    from memory.session_store import parse_frequency_doses_per_day

    assert parse_frequency_doses_per_day("每周三次") == pytest.approx(3 / 7)
    assert parse_frequency_doses_per_day("每周四次") == pytest.approx(4 / 7)
    assert parse_frequency_doses_per_day("每周一、三、五") == pytest.approx(
        3 / 7
    )
    assert parse_frequency_doses_per_day("每周三") == pytest.approx(1 / 7)


def test_half_tablet_dose_units():
    from memory.session_store import medication_dose_units

    assert medication_dose_units("0.5片", "片") == 0.5
    assert medication_dose_units("0.25片", "片") == 0.25
    assert medication_dose_units("2片", "片") == 2


def test_stopped_medication_stops_expected_doses():
    from memory.session_store import expected_doses_for_period

    now = datetime.now()
    started = (now - timedelta(days=20)).isoformat(timespec="minutes")
    stopped = (now - timedelta(days=1)).isoformat(timespec="minutes")
    expected = expected_doses_for_period(
        "每日一次", started, now, 7, stopped
    )
    assert expected == 6


def test_dossier_daily_steps_uses_day_total(client_and_store):
    client, store = client_and_store
    today = datetime.now()
    store.set_metric_target("妈妈", "steps", 8000)
    store.add_metric(
        "妈妈", "steps", 4000,
        measured_at=today.replace(hour=8, minute=0).isoformat(
            timespec="minutes"
        ),
    )
    store.add_metric(
        "妈妈", "steps", 5000,
        measured_at=today.replace(hour=18, minute=0).isoformat(
            timespec="minutes"
        ),
    )

    body = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    ).text

    steps_line = next(
        line for line in body.splitlines() if line.startswith("- 步数")
    )
    assert "低于目标" not in steps_line


def test_attachment_ref_option_injected_for_old_record():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("async function openAttachmentUploadForRecord")
    body = app_js[start:start + 2200]
    assert "els.attachmentRef.appendChild(option)" in body
    assert "attachmentRefLabel(kind, item)" in body


def test_profile_switch_detaches_session():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function detachSessionForProfileChange()" in app_js
    assert app_js.count("detachSessionForProfileChange();") >= 2


def test_legacy_owner_marker_is_migrated(tmp_path):
    from core.storage_paths import attachment_dir_for_db

    legacy = tmp_path / "attachments"
    legacy.mkdir()
    (legacy / ".pdf").write_bytes(b"x")
    # #706 及更早版本把标记写在附件目录内部
    (legacy / ".medix-owner").write_text(
        "family.db", encoding="utf-8"
    )
    (legacy / "abc123.pdf").write_bytes(b"x")
    (tmp_path / "family.db").write_bytes(b"db")

    # 先打开默认库：不能接管已被自定义库认领的目录
    default_dir = attachment_dir_for_db(tmp_path / "sessions.db")
    assert default_dir != legacy
    # 旧标记迁移到父目录，且不会在附件目录内残留
    assert (tmp_path / ".medix-attachments-owner").read_text(
        encoding="utf-8"
    ) == "family.db"
    assert not (legacy / ".medix-owner").exists()

    # 自定义库仍使用共享目录
    assert attachment_dir_for_db(tmp_path / "family.db") == legacy
