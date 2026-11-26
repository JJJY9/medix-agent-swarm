# -*- coding: utf-8 -*-
"""第一百二十二轮审查修复的回归测试。"""
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"
LATER = "2026-06-02T09:00:00"


def _store(tmp_path, tag="s"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


# ---------------------------------------------------------------- [1]
def test_insights_no_nameerror(tmp_path, monkeypatch):
    # 依从良好的单档案健康提示不再 NameError（第一百二十二轮 [1]）
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path, "s1")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        client = TestClient(api_server.app)
        store.save_profile("妈妈", {})
        day = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        med = store.add_medication(
            "妈妈", "氨氯地平", dose="5 mg", frequency="每日一次",
            started_at=day,
        )
        mid = int(med["id"])
        for offset in (2, 1, 0):
            taken = (
                datetime.now() - timedelta(days=offset)
            ).strftime("%Y-%m-%d") + "T08:00"
            store.add_medication_log(mid, taken)
        r = client.get(
            "/api/health/insights",
            params={"days": 30, "profile": "妈妈"},
        )
        assert r.status_code == 200
        d = client.get(
            "/api/health/dossier.md",
            params={"days": 30, "profile": "妈妈"},
        )
        assert d.status_code == 200
    finally:
        store.close()


# ---------------------------------------------------------------- [2]
def test_third_person_stop_keeps_patient_drug():
    # 第三人称停药说的是家属，不适用于患者本人在用药
    # （第一百二十二轮 [2]）
    r = _red(
        "我爸在吃阿司匹林，我在吃华法林，他去年停了，今天头撞到门框",
        {"age": "40岁"},
    )
    assert "抗凝治疗期间头部外伤" in r["matches"], r
    r = _red("我在吃华法林，她去年停了，今天头撞到门框", {"age": "40岁"})
    assert r["level"] == "emergency", r
    result = check_medication_safety(
        "我爸在吃阿司匹林，我在吃华法林，他去年停了，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_third_person_family_subject_still_stops():
    # 家属主语本身的停药仍按家属处理（第一百二十二轮 [2]）
    r = _red(
        "我儿子在吃华法林，他上个月停了，今天头撞到门框",
        {"age": "40岁"},
    )
    assert r["level"] == "none", r


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "怀孕8周，牙龈出血、阴道有血",
        "怀孕8周，牙龈出血、下面有血",
        "怀孕8周，牙龈出血、内裤上有血",
        "怀孕8周，牙龈出血和见红",
        "怀孕8周，牙龈出血及见红",
        "怀孕8周，牙龈出血与见红",
    ],
)
def test_enumeration_gum_not_swallow_vaginal(text):
    # 牙龈豁免不能吞掉并列的孕产期出血（第一百二十二轮 [3]）
    r = _red(text)
    assert "孕产期出血" in r["matches"], r


def test_enumeration_gum_control():
    r = _red("怀孕8周，牙龈出血")
    assert r["level"] == "none", r


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "肾移植术后，今天发烧38.3度",
        "肝移植后，今天发烧了",
        "心脏移植术后，今天发烧",
        "肺移植后，今天发烧",
        "我吃抗排异药，今天发烧",
    ],
)
def test_organ_transplant_fever(text):
    # 实体器官移植与抗排异药同样属免疫抑制（第一百二十二轮 [4]）
    r = _red(text)
    assert "免疫功能低下伴发热" in r["matches"], r


def test_organ_transplant_control():
    assert _red("什么是肾移植")["level"] == "none"


# ---------------------------------------------------------------- [5]
def test_origin_map_truncation_fail_closed(tmp_path):
    # 来源映射裁剪后缺失 origin 的行不能当作本机创建
    # （第一百二十二轮 [5]）
    store = _store(tmp_path, "s5")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        meds = [
            {"id": i, "profile": "妈妈", "name": "药%d" % i,
             "dose": "1mg", "frequency": "每日一次", "note": "",
             "active": True, "created_at": CREATED, "updated_at": CREATED}
            for i in range(1, 4201)
        ]
        store.import_data({"source_id": "a" * 32, "medications": meds})
        origin = store._get_import_origin_map("medications")
        assert "1" not in origin
        assert (
            store.get_app_state("import_origin_truncated:medications")
            == "1"
        )
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE medications SET dose = '20mg',"
                " updated_at = '2026-06-02T09:00:00' WHERE id = 1"
            )
        backup = {
            "source_id": "b" * 32,
            "medications": [
                {"id": 1, "profile": "妈妈", "name": "药1",
                 "dose": "1mg", "frequency": "每日一次", "note": "",
                 "active": True, "created_at": CREATED,
                 "updated_at": CREATED},
            ],
        }
        added = store.import_data(backup).get("medications")
        rows = store._conn.execute(
            "SELECT dose FROM medications WHERE name = '药1'"
        ).fetchall()
        doses = sorted(str(r["dose"]) for r in rows)
        assert added == 1
        assert doses == ["1mg", "20mg"]
    finally:
        store.close()


# ---------------------------------------------------------------- [6]
def test_foreign_twin_reminder_inserted(tmp_path):
    # 外来源同 id 同秒、尾字段不同：补插而不是认领
    # （第一百二十二轮 [6]）
    local = _store(tmp_path, "s6a")
    try:
        local._now = lambda: CREATED
        local.save_profile("妈妈", {})
        local.add_reminder(
            "复查", "2026-06-08", note="n1", profile="妈妈",
            repeat_days=7,
        )
        backup = {
            "source_id": "b" * 32,
            "reminders": [
                {"id": 1, "profile": "妈妈", "title": "复查",
                 "due_at": "2026-06-08", "note": "", "repeat_days": 0,
                 "done": 0, "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        assert local.import_data(backup).get("reminders") == 1
        rows = local.list_reminders(True, "妈妈")
        assert len(rows) == 2
        assert local.import_data(backup).get("reminders") == 0
        assert len(local.list_reminders(True, "妈妈")) == 2
    finally:
        local.close()


def test_foreign_terminal_med_inserted(tmp_path):
    # 本地停药行与备份在用行同秒同内容：补插备份行
    # （第一百二十二轮 [6]）
    local = _store(tmp_path, "s6m")
    try:
        local._now = lambda: CREATED
        local.save_profile("妈妈", {})
        local.add_medication(
            "妈妈", "二甲双胍", dose="5mg", frequency="每日两次",
            active=False, stopped_at="2026-06-01", stop_reason="医嘱",
        )
        backup = {
            "source_id": "c" * 32,
            "medications": [
                {"id": 1, "profile": "妈妈", "name": "二甲双胍",
                 "dose": "5mg", "frequency": "每日两次", "note": "",
                 "active": True, "created_at": CREATED,
                 "updated_at": CREATED},
            ],
        }
        assert local.import_data(backup).get("medications") == 1
        assert len(local.list_medications("妈妈", None)) == 2
    finally:
        local.close()


# ---------------------------------------------------------------- [7]
def test_latest_risk_by_full_timestamp():
    # 恢复备份后 message_id 顺序与时间相反时仍按时间取最新
    # （第一百二十二轮 [7]）
    answers = [
        {"profile": "妈妈", "answered_at": "2026-11-27T20:05:00",
         "content": "风险等级：高危。请立即急诊。", "message_id": 2},
        {"profile": "妈妈", "answered_at": "2026-11-27T08:05:00",
         "content": "风险等级：低危，可以观察。", "message_id": 4},
    ]
    latest = api_server._latest_parseable_risk(answers)
    assert (latest.get("妈妈") or {}).get("risk") == "high"
    latest2 = api_server._latest_parseable_risk(list(reversed(answers)))
    assert (latest2.get("妈妈") or {}).get("risk") == "high"


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "history,text,label",
    [
        ("停药半年后发现怀孕了", "阴道出血", "孕产期出血"),
        (
            "停用华法林两年后改成利伐沙班了",
            "今天头撞到门框",
            "抗凝治疗期间头部外伤",
        ),
        ("停用地塞米松一年后改成泼尼松", "发烧39度", "免疫功能低下伴发热"),
    ],
)
def test_long_stop_month_year_tail(history, text, label):
    # 月/年远期停药尾部同样保留现孕/换药状态（第一百二十二轮 [8]）
    r = _red(text, {"history": history})
    assert label in r["matches"], r


# ---------------------------------------------------------------- [9]
@pytest.mark.parametrize(
    "text",
    [
        "停用华法林一个星期了，能吃布洛芬吗",
        "停用华法林一个礼拜了，能吃布洛芬吗",
        "停用华法林两个礼拜了，能吃布洛芬吗",
        "I stopped warfarin 2 weeks ago, can I take ibuprofen?",
        "I stopped warfarin ten days ago, can I take ibuprofen?",
        "It's been 3 days since I stopped warfarin,"
        " can I take ibuprofen?",
        "I've been off warfarin since last week,"
        " can I take ibuprofen?",
        "I came off warfarin last week, can I take ibuprofen?",
        "A week ago I stopped warfarin, can I take ibuprofen?",
    ],
)
def test_recent_stop_synonyms(text):
    # 星期/礼拜与英文 since/ago 语序按近期停药（第一百二十二轮 [9]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


@pytest.mark.parametrize(
    "text",
    [
        "I stopped taking warfarin 3 weeks ago,"
        " can I take ibuprofen?",
        "停用华法林3周了，能吃阿司匹林吗",
        "I stopped warfarin 30 days ago, can I take ibuprofen?",
    ],
)
def test_recent_stop_synonyms_long_control(text):
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" not in _titles(result), result


# ---------------------------------------------------------------- [10]
def test_repeated_import_idempotent_after_noop_save(tmp_path):
    # 无变化保存后重复导入同一备份不补插重复提醒
    # （第一百二十二轮 [10]）
    s1 = _store(tmp_path, "s10a")
    b = _store(tmp_path, "s10b")
    target = _store(tmp_path, "s10t")
    try:
        for st in (s1, b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        s1.add_reminder("吃药", "2026-06-08", note="", profile="妈妈")
        s1.add_reminder("测血压", "2026-06-08", note="", profile="妈妈")
        backup_s1 = s1.export_data()
        b.import_data(backup_s1)
        row_med = next(
            r for r in b.list_reminders(False, "妈妈")
            if r["title"] == "吃药"
        )
        b.complete_reminder(int(row_med["id"]))
        b._now = lambda: "2026-06-02T08:00:00"
        target.import_data(b.export_data())
        row_bp = next(
            r for r in target.list_reminders(True, "妈妈")
            if r["title"] == "测血压"
        )
        target._now = lambda: "2026-06-03T09:00:00"
        target.update_reminder(int(row_bp["id"]), note="")
        counts = [target.import_data(backup_s1).get("reminders")
                  for _ in range(3)]
        rows = target.list_reminders(True, "妈妈")
        assert counts == [0, 0, 0], counts
        assert len([r for r in rows if r["title"] == "吃药"]) == 1
        assert len([r for r in rows if r["title"] == "测血压"]) == 1
    finally:
        for st in (s1, b, target):
            st.close()


# ---------------------------------------------------------------- [11]
def test_shared_drop_clears_owner_after_unlink(tmp_path):
    # 共享预约脱钩后清 owner/备注，改期不回挂本就诊
    # （第一百二十二轮 [11]）
    store = _store(tmp_path, "s11")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        v1 = int(store.add_visit_record(
            "妈妈", visit_date="2026-11-01", diagnosis="高血压",
            next_followup_date="2026-12-01",
        )["id"])
        v2 = int(store.add_visit_record(
            "妈妈", visit_date="2026-11-02", diagnosis="糖尿病",
            next_followup_date="2026-12-01",
        )["id"])
        appt = int(store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-01T09:00",
            note=f"由就诊记录 #{v1} 自动创建", owner_visit_id=v1,
        )["id"])
        rem = int(store.add_reminder(
            "复诊：高血压", "2026-11-30", note="复诊", profile="妈妈",
            appointment_id=appt,
        )["id"])
        store.set_visit_followup_links(v1, appt, rem)
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = ?,"
                " followup_reminder_id = ? WHERE id = ?",
                (appt, rem, v2),
            )
        store._now = lambda: "2026-11-27T10:00:00"
        store.update_visit_record_partial(
            v1, next_followup_date="2026-12-11"
        )
        dropped = store.get_visit_record(v1)
        assert int(dropped["followup_appointment_id"] or 0) == 0
        store.update_appointment_partial(
            appt, scheduled_at="2026-12-08T09:00"
        )
        after = store.get_visit_record(v1)
        assert int(after["followup_appointment_id"] or 0) == 0
        assert str(after["next_followup_date"]) == "2026-12-11"
        appt_row = store.get_appointment(appt)
        assert int(appt_row.get("owner_visit_id") or 0) == 0
        assert "由就诊记录" not in str(appt_row.get("note") or "")
    finally:
        store.close()


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text",
    [
        "超声提示低危 结节，定期随访即可。",
        "注意避免高危 行为，正确使用安全套。",
        "高危ＨＰＶ持续感染需要复查。",
        "高风险 人群建议筛查。",
    ],
)
def test_risk_text_normalized_objects(text):
    # 空白/全角不破坏对象名词守卫（第一百二十二轮 [12]）
    assert api_server.parse_risk_level(text) == ""


def test_risk_text_normalized_control():
    assert api_server.parse_risk_level("情况高危，请立即就医。") == "high"


# ---------------------------------------------------------------- [13]
def test_today_started_sample_not_shown():
    # 仅由当天新开药贡献的打卡率不再显示用药打卡提示
    # （第一百二十二轮 [13]）
    today = datetime.now().strftime("%Y-%m-%d")
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={
            "worst_rate": None,
            "adherence": {"expected": 3, "rate": 0},
            "oldest_started_at": today + "T08:00:00",
        },
        medication_lab={"count": 1, "level": "info",
                        "alerts": [{"title": "头孢类与酒精"}]},
        vaccinations={}, appointments={},
    )
    assert "用药打卡" not in str(attention.get("label") or ""), attention


def test_old_low_rate_sample_shown():
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={
            "worst_rate": 0,
            "worst_started_at": "2026-06-10T08:00:00",
            "adherence": {"expected": 1, "rate": 0},
            "oldest_started_at": "2026-06-10T08:00:00",
        },
        medication_lab={}, vaccinations={}, appointments={},
    )
    assert "用药打卡" in str(attention.get("label") or ""), attention


# ---------------------------------------------------------------- [14]
def test_web_restore_success_stashes_draft():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("initProfiles().finally(")
    block = source[start:start + 900]
    assert "if (deferredCrossOwnerDraft) {" in block
    assert "restoreDeferredCrossOwnerDraft();" in block


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize(
    "text",
    [
        "不确定有没有怀孕，大便见红",
        "孕8周，大便见红",
        "孕8周，尿里见红",
        "孕8周，痰里见红",
        "孕8周，刷牙见红",
        "孕8周，口腔见红",
    ],
)
def test_see_red_non_pregnancy_sites(text):
    # 大便/尿/痰/口腔见红不是孕产期出血（第一百二十二轮 [15]）
    assert _red(text)["level"] == "none", _red(text)


def test_see_red_controls():
    assert "孕产期出血" in _red("孕8周，见红")["matches"]
    assert _red("孕8周，牙龈见红")["level"] == "none"


# ---------------------------------------------------------------- [16]
@pytest.mark.parametrize(
    "text,expect",
    [
        ("华法林（停药30天）、布洛芬", False),
        ("华法林（停药15天）", False),
        ("华法林（停药14天）", True),
        ("华法林（停药2周）", True),
    ],
)
def test_context_names_recent_bounds(text, expect):
    # 家庭总览停药窗口与 core 对齐（第一百二十二轮 [16]）
    names = api_server._context_medication_names({"medications": text})
    assert any("华法林" in n for n in names) is expect, names


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "text",
    [
        "华法林 3毫克/日（已停用两年）",
        "华法林 10毫升/次（已停用）",
    ],
)
def test_context_names_chinese_unit_slash(text):
    # 中文单位斜杠剂量同样不是药名分隔（第一百二十二轮 [17]）
    names = api_server._context_medication_names({"medications": text})
    assert not any("华法林" in n for n in names), names


# ---------------------------------------------------------------- [18]
def test_import_dedupes_owner_appointment(tmp_path):
    # 备注无标记的 owner 型本地预约也纳入去重（第一百二十二轮 [18]）
    target = _store(tmp_path, "s18")
    try:
        target._now = lambda: CREATED
        target.save_profile("妈妈", {})
        visit = {
            "visit_date": "2026-11-01", "diagnosis": "高血压",
            "department": "内科", "doctor": "张医生", "facility": "医院",
            "next_followup_date": "2026-12-20",
        }
        target.import_data({
            "source_id": "a" * 32,
            "visit_records": [
                {"id": 10, "profile": "妈妈", **visit,
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 40,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-20T09:00", "status": "upcoming",
                 "owner_visit_id": 10, "note": "",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": "2026-11-30", "note": "复诊 2026-11-30",
                 "appointment_id": 0, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        })
        target.import_data({
            "source_id": "b" * 32,
            "visit_records": [
                {"id": 50, "profile": "妈妈", **visit,
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 0,
                 "created_at": CREATED, "updated_at": LATER},
            ],
            "appointments": [
                {"id": 70, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-20T09:00", "status": "upcoming",
                 "owner_visit_id": 50,
                 "note": "由就诊记录 #50 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [
                {"id": 80, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": "2026-11-30", "note": "复诊 2026-12-20",
                 "appointment_id": 70, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        })
        vids = [int(v["id"]) for v in target.list_visit_records("妈妈")]
        rows = target._conn.execute(
            "SELECT owner_visit_id FROM appointments"
            " WHERE status = 'upcoming'"
        ).fetchall()
        owned = {}
        for row in rows:
            owner = int(row["owner_visit_id"] or 0)
            if owner in vids:
                owned[owner] = owned.get(owner, 0) + 1
        assert all(count == 1 for count in owned.values()), owned
        assert len(target.list_reminders(False, "妈妈")) <= 1
    finally:
        target.close()


# ---------------------------------------------------------------- [19]
def test_web_launch_inflight_preserved():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "launchNewSessionSettled" in source
    start = source.index("async function send(")
    block = source[start:start + 2600]
    assert (
        "if (pendingLaunchNewSession && !launchNewSessionSettled)"
        in block
    )
    preserve = block.find("preserveOtherSessionInflight('', question);")
    race = block.find("await Promise.race([")
    assert 0 <= preserve < race


# ---------------------------------------------------------------- [20]
def test_web_consume_keeps_slot():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("function consumeDeferredDraftForSession(")
    block = source[start:start + 1400]
    body = block[block.index("if (!raw) return;"):]
    assert body.find("if (els.input.value.trim()) {") < body.find(
        "els.input.value = payload.text;"
    )
    assert "保留暂存槽" in body
