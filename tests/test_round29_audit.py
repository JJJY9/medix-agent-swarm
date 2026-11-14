"""第二十九轮审查：导入/别名/墓碑/规则/家庭累计/PWA 回归测试。"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path):
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        yield session_store
    finally:
        session_store.close()


def test_import_moved_record_is_not_duplicated(store):
    visit = store.add_visit_record("妈妈", visit_date="2026-11-01")
    backup = store.export_data()
    # 记录被改挂到另一位成员。时间戳只有秒精度：跨档案认领要求能看到
    # 「被编辑过」的证据（updated_at != created_at），这里让改动落在
    # 下一秒，模拟真实用户操作
    time.sleep(1.05)
    store.update_visit_record_partial(visit["id"], profile="爸爸")

    result = store.import_data(backup)

    assert result["visit_records"] == 0
    rows = store.list_visit_records(None, 0, "", 100)
    assert len(rows) == 1
    assert rows[0]["profile"] == "爸爸"


def test_import_after_reminder_title_edit_does_not_duplicate(store):
    reminder = store.add_reminder(
        "复查血压", "2026-12-01", "备注", "", "妈妈"
    )
    backup = store.export_data()
    store.update_reminder(
        reminder["id"], title="复查血压（心内科）", note="新的备注"
    )

    result = store.import_data(backup)

    assert result["reminders"] == 0
    titles = [
        row["title"] for row in store.list_reminders(True, "妈妈")
    ]
    assert titles == ["复查血压（心内科）"]


def test_alias_resolution_stops_at_reused_name(store):
    store.add_medication("妈妈", "药A", "1片", "每日一次")
    store.rename_profile("妈妈", "母亲")
    # 「妈妈」被新档案复用（新患者数据）
    store.add_medication("妈妈", "药B", "1片", "每日一次")
    # 原患者的档案再次改名
    store.rename_profile("母亲", "老妈")

    assert store.resolve_profile_alias("妈妈") == "妈妈"
    assert store.resolve_profile_alias("母亲") == "老妈"


def test_large_metadata_no_longer_breaks_import_limits():
    from memory.session_store import _import_payload_problems

    blob = "y" * (900 * 1024)
    payload = {
        "sessions": [{
            "id": "s1",
            "messages": [
                {
                    "role": "user",
                    "content": "x",
                    "metadata": {"blob": blob},
                }
                for _ in range(36)
            ],
        }],
    }

    # 元数据总量约 31MB，超过旧的 30M「消息总量」口径；
    # 现在元数据只做单条上限，不再计入总量
    assert _import_payload_problems(payload) == []


def test_family_overview_uses_latest_day_steps_total(tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "family.db"))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        session_store.save_profile("妈妈", {"sex": "女"})
        session_store.set_metric_target("妈妈", "steps", 8000)
        yesterday = (datetime.now() - timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        session_store.add_metric(
            "妈妈", "steps", 4000, measured_at=f"{yesterday}T08:00"
        )
        session_store.add_metric(
            "妈妈", "steps", 5000, measured_at=f"{yesterday}T18:00"
        )
        client = TestClient(api_server.app)

        data = client.get("/api/family/overview").json()

        steps = next(
            item for item in data["profiles"][0]["metrics"]
            if item["metric"] == "steps"
        )
        # 昨日累计 9000 已达标：不能因最后一条 5000 报「未达标」
        assert steps["over_target"] is False
    finally:
        monkeypatch.undo()
        session_store.close()


def test_persistent_session_tombstone_across_reopen(tmp_path):
    from memory.session_store import SessionStore

    db_path = str(tmp_path / "tombstone.db")
    first = SessionStore(db_path)
    try:
        assert first.save_exchange("s1", "问题", "回答") is not None
        assert first.delete_session("s1") is True
    finally:
        first.close()

    second = SessionStore(db_path)
    try:
        assert second.is_session_deleted("s1") is True
        assert second.save_exchange("s1", "新问题", "新回答") is None
        # 显式重建会话后恢复写入
        second.ensure_session("s1", title="新会诊")
        assert second.save_exchange("s1", "新问题", "新回答") is not None
    finally:
        second.close()


def test_schema_version_recorded(store):
    row = store._conn.execute("PRAGMA user_version").fetchone()
    assert int(row[0]) == 1


def test_condition_double_negation_is_not_excluded():
    from core.condition_drug_safety import _negated_mention

    assert _negated_mention("不排除肝硬化", "肝硬化") is False
    assert _negated_mention("无法排除肝硬化", "肝硬化") is False
    assert _negated_mention("尚未排除肝硬化", "肝硬化") is False
    assert _negated_mention("医生排除了肝硬化", "肝硬化") is True
    assert _negated_mention("无肝硬化", "肝硬化") is True


def test_topical_steroid_does_not_mask_oral_use():
    from core.medication_safety import _find_drugs

    found = _find_drugs(
        "泼尼松片口服，氢化可的松乳膏外用", "corticosteroids"
    )
    assert "泼尼松" in found
    assert "氢化可的松" not in found
    # 只有局部剂型时不报全身激素
    assert _find_drugs("氢化可的松乳膏外用", "corticosteroids") == []


def test_educational_red_flag_text_is_not_validated_as_symptom():
    from constraints.validator import ConstraintValidator

    validator = ConstraintValidator()
    result = validator.validate_output(
        "diagnostic_agent",
        "糖尿病酮症酸中毒是指胰岛素严重不足导致的一种急性并发症。"
        "以上信息仅供参考，不能替代医生的诊断和治疗。",
        user_input="糖尿病有哪些并发症？",
    )

    assert "高危症状未建议就医" not in result["violations"]


def test_degraded_checked_before_constraint_rewrite():
    source = (ROOT / "swarm" / "swarm_coordinator.py").read_text(
        encoding="utf-8"
    )
    degraded_pos = source.index(
        "degraded = is_degraded_answer(raw_answer)"
    )
    enforce_pos = source.index(
        "final_answer = self._enforce_final_constraints("
    )
    # 约束兜底会在道歉前插入「⚠️ 重要提醒」，判定必须在改写之前
    assert degraded_pos < enforce_pos


def test_session_id_stored_per_tab():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "safeSessionGet('medix.session.tab')" in app_js
    assert "function persistSessionId(" in app_js
    assert "function clearPersistedSessionId(" in app_js


def test_notification_click_does_not_navigate_focused_window():
    sw_js = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")

    assert "medix-open-reminders" in sw_js
    assert "medix-open-reminders-ack" in sw_js
    focused_pos = sw_js.index("clients.matchAll")
    assert sw_js.index("client.focused", focused_pos) > focused_pos
    # 任何已存在的窗口都先发消息并等待 ack；未收到才回退整页导航。
    # 点击系统通知时窗口通常是未聚焦状态，只看 focused 分支并不够。
    message_pos = sw_js.index("targetWindow.postMessage")
    ack_pos = sw_js.index("await waitForReminderAck(targetWindow.id")
    navigate_pos = sw_js.index("targetWindow.navigate")
    assert message_pos < ack_pos < navigate_pos


def test_update_banner_can_be_dismissed():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "function hideUpdateBanner()" in app_js
    assert "知道了" in app_js
