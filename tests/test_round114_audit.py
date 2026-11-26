# -*- coding: utf-8 -*-
"""第一百一十四轮审查修复的回归测试。"""
from datetime import date, timedelta
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path, tag="s"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _day(offset):
    return (date.today() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _labels(text, context=None):
    return _red(text, context)["matches"]


def _level(text, context=None):
    return _red(text, context)["level"]


def _titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


WARFARIN = {"age": "60岁", "medications": "华法林 3mg 每日一次"}


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "孕8周，刚才上厕所见血了",
        "孕8周，见血了",
        "怀孕8周，见血了",
    ],
)
def test_pregnancy_bleeding_jianxue(text):
    # 「见血」与「见红」同为孕产期出血表述（第一百一十四轮 [1]）
    assert "孕产期出血" in _labels(text), _red(text)


def test_pregnancy_bleeding_jianxue_controls():
    assert "孕产期出血" in _labels("孕8周，刚才上厕所见红了")
    assert _level("孕8周，没有见血") == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "昨晚停了华法林，能吃布洛芬吗",
        "昨夜停了华法林，能吃布洛芬吗",
        "前晚停了华法林，能吃布洛芬吗",
        "I stopped taking warfarin last night, can I take"
        " ibuprofen?",
    ],
)
def test_recent_stop_overnight(text):
    # 昨晚/昨夜/前晚/last night 都是近期停药（第一百一十四轮 [2]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_recent_stop_overnight_control():
    result = check_medication_safety("两年前停了华法林，能吃布洛芬吗", {})
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [3]
def test_merge_backup_edited_twin_not_swallowed(tmp_path):
    # 他机备份与本地编辑过的同号行必须共存；来源集合不被污染
    # （第一百一十四轮 [3]）
    src_a = _store(tmp_path, "a")
    src_b = _store(tmp_path, "b")
    target = _store(tmp_path, "t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_medication(
            "妈妈", "布洛芬", dose="50mg", frequency="每日两次",
        )
        backup_a = src_a.export_data()
        src_b.add_medication(
            "妈妈", "布洛芬", dose="100mg", frequency="每日两次",
        )
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        med = target.list_medications("妈妈", None)[0]
        target._now = lambda: "2026-10-01T09:00:00"
        target.update_medication(int(med["id"]), dose="75mg")
        assert target.import_data(backup_b).get("medications") == 1
        rows = target.list_medications("妈妈", None)
        assert sorted(str(r["dose"]) for r in rows) == ["100mg", "75mg"]
        assert target.import_data(backup_b).get("medications") == 0
        origin_map = target._get_import_origin_map("medications")
        edited = next(r for r in rows if str(r["dose"]) == "75mg")
        assert len(origin_map.get(str(edited["id"])) or []) == 1
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [4]
def test_menstrual_nickname_trauma_red_flag():
    # 「大姨妈期间摔到头」先归一月经过滤；「妈」不能吞掉头外伤红旗
    # （第一百一十四轮 [4]）
    assert (
        "抗凝治疗期间头部外伤"
        in _labels("大姨妈期间摔到头", WARFARIN)
    )
    assert (
        "抗凝治疗期间头部外伤"
        in _labels("月经期间摔到头", WARFARIN)
    )
    assert (
        "抗凝治疗期间头部外伤"
        in _labels("大姨妈期间，摔到头", WARFARIN)
    )


def test_menstrual_nickname_trauma_control():
    assert _level("今天发烧39度", {"history": "大姨妈在化疗"}) == "none"


# ---------------------------------------------------------------- [5][12]
@pytest.mark.parametrize(
    "text",
    [
        "孕8周，没有出血。今天做了B超。",
        "孕8周，没有出血，做了B超",
        "孕8周，阴道没有出血，太累了",
    ],
)
def test_pregnancy_bleeding_le_adjacent_only(text):
    # 后续分句的「了」不是出血停止证据（第一百一十四轮 [5][12]）
    assert _level(text) == "none", _red(text)


def test_pregnancy_bleeding_le_controls():
    assert "孕产期出血" in _labels("怀孕8周，阴道没有出血了")
    assert _level("孕8周，阴道不再流血了") == "none"


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "我想把华法林停掉，能吃布洛芬吗",
        "我打算把华法林停了，能吃布洛芬吗",
        "准备把华法林停掉，能吃布洛芬吗",
    ],
)
def test_intent_ba_stop_still_active(text):
    # 「想/打算/准备把…停掉」是意向，当前仍在用药（第一百一十四轮 [6]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_intent_ba_stop_control():
    result = check_medication_safety(
        "我已经把华法林停掉了，能吃布洛芬吗", {}
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [7]
def test_own_snapshot_restore_idempotent(tmp_path):
    # 本机自导出快照恢复同秒孪生行不应补插旧副本（第一百一十四轮 [7]）
    old = _store(tmp_path, "o")
    target = _store(tmp_path, "t7")
    try:
        old._now = lambda: CREATED
        old.save_profile("妈妈", {})
        old.add_reminder("复查", _day(7), note="n1", profile="妈妈")
        old.add_reminder("吃药", _day(19), note="n2", profile="妈妈")
        backup_old = old.export_data()
        target._now = lambda: CREATED
        target.save_profile("妈妈", {})
        target.import_data(backup_old)
        target._now = lambda: "2026-06-02T08:00:00"
        snapshot = target.export_data()
        first = next(
            r
            for r in target.list_reminders(True, "妈妈")
            if r["title"] == "复查"
        )
        target._now = lambda: "2026-10-01T09:00:00"
        target.update_reminder(int(first["id"]), due_at=_day(9))
        assert target.import_data(snapshot).get("reminders") == 0
        rows = target.list_reminders(True, "妈妈")
        assert len(rows) == 2
    finally:
        old.close()
        target.close()


# ---------------------------------------------------------------- [8]
def test_schedule_adopt_respects_other_visit_link(tmp_path):
    # 无归属同日预约仍被其它就诊链接时不能抢（第一百一十四轮 [8]）
    store = _store(tmp_path, "f8")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        v2 = int(
            store.add_visit_record(
                "妈妈", visit_date="2026-10-01", diagnosis="高血压",
                next_followup_date=_day(20),
            )["id"]
        )
        appt = int(
            store.add_appointment(
                "妈妈", "复诊：高血压",
                scheduled_at=_day(20) + "T09:00",
            )["id"]
        )
        reminder = int(
            store.add_reminder(
                "复诊：高血压", _day(19), note="复诊",
                profile="妈妈", appointment_id=appt,
            )["id"]
        )
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = ?,"
                " followup_reminder_id = ?, updated_at = ? WHERE id = ?",
                (appt, reminder, CREATED, v2),
            )
        v3 = int(
            store.add_visit_record(
                "妈妈", visit_date="2026-10-05", diagnosis="高血压",
                next_followup_date=_day(20),
            )["id"]
        )
        result = api_server._schedule_followup_sync(store, v3)
        v2_row = store.get_visit_record(v2)
        reminders = store.list_reminders(True, "妈妈")
        assert result.get("appointment_created") is True
        assert int(v2_row["followup_appointment_id"] or 0) == appt
        assert int(v2_row["followup_reminder_id"] or 0) == reminder
        assert any(int(r["id"]) == reminder for r in reminders)
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_context_names_comma_stop_annotation():
    # 「布洛芬，两个月前停了」的纯时长 head 是停药标注（第一百一十四轮 [9]）
    names = api_server._context_medication_names(
        {"medications": "华法林 3mg 每日一次，布洛芬，两个月前停了"}
    )
    result = api_server._evaluate_medication_safety(
        names, [], "62", "女", [], []
    )
    assert "华法林与非甾体抗炎药" not in _titles(result), result
    names = api_server._context_medication_names(
        {"medications": "华法林 3mg 每日一次，布洛芬"}
    )
    result = api_server._evaluate_medication_safety(
        names, [], "62", "女", [], []
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [10]
@pytest.mark.parametrize(
    "text",
    [
        "华法林 3mg 每日一次，孩子吃布洛芬",
        "华法林 3mg 每日一次，家里老人吃布洛芬",
        "华法林 3mg 每日一次，我爹吃布洛芬",
        "华法林 3mg 每日一次，丈母娘吃布洛芬",
    ],
)
def test_family_marker_bare_terms(text):
    # 裸家属称谓（孩子/爹/丈母娘/家里老人）不把家属用药算到患者
    # （第一百一十四轮 [10]）
    names = api_server._context_medication_names({"medications": text})
    result = api_server._evaluate_medication_safety(
        names, [], "62", "女", [], []
    )
    assert "华法林与非甾体抗炎药" not in _titles(result), result


# ---------------------------------------------------------------- [11][15]
def test_marker_dedupe_leaves_existing_plan(tmp_path):
    # 标记型遗留去重只处理本次导入；既有计划与跨成员标记不动
    # （第一百一十四轮 [11][15]）
    store = _store(tmp_path, "f11")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        v1 = int(
            store.add_visit_record(
                "爸爸", visit_date="2026-10-01", diagnosis="高血压",
                next_followup_date=_day(20),
            )["id"]
        )
        a1 = int(
            store.add_appointment(
                "爸爸", "复诊：高血压",
                scheduled_at=_day(20) + "T09:00",
                note=f"由就诊记录 #{v1} 自动创建", owner_visit_id=v1,
            )["id"]
        )
        r1 = int(
            store.add_reminder(
                "复诊：高血压", _day(19), note="复诊",
                profile="爸爸", appointment_id=a1,
            )["id"]
        )
        mom_appt = int(
            store.add_appointment(
                "妈妈", "复诊：高血压",
                scheduled_at=_day(10) + "T09:00",
                note=f"由就诊记录 #{v1} 自动创建",
            )["id"]
        )
        forged = {
            "visit_records": [
                {
                    "id": 10, "profile": "爸爸",
                    "visit_date": "2026-10-01", "diagnosis": "高血压",
                    "next_followup_date": _day(20),
                    "followup_appointment_id": 0,
                    "followup_reminder_id": 0,
                },
            ],
            "appointments": [
                {
                    "id": 30, "profile": "爸爸", "title": "复诊：高血压",
                    "scheduled_at": _day(20), "status": "upcoming",
                    "owner_visit_id": 0,
                    "note": "由就诊记录 #10 自动创建",
                    "created_at": CREATED, "updated_at": CREATED,
                },
                {
                    "id": 31, "profile": "爸爸", "title": "复诊：高血压",
                    "scheduled_at": _day(24) + "T09:00",
                    "status": "upcoming", "owner_visit_id": 0,
                    "note": "由就诊记录 #10 自动创建",
                    "created_at": CREATED, "updated_at": CREATED,
                },
            ],
        }
        store.import_data(forged)
        assert store.get_reminder(r1) is not None
        a1_row = store.get_appointment(a1)
        mom_row = store.get_appointment(mom_appt)
        assert int(a1_row.get("owner_visit_id") or 0) == v1
        assert int(mom_row.get("owner_visit_id") or 0) == 0
        assert (
            f"由就诊记录 #{v1} 自动创建"
            in str(mom_row.get("note") or "")
        )
        assert store._conn.in_transaction is False
    finally:
        store.close()


# ---------------------------------------------------------------- [14]
def test_marker_dedupe_persists_without_source_id(tmp_path):
    # 无 source_id 的旧备份去重只提交一次、重开仍生效
    # （第一百一十四轮 [14]）
    path = Path(tmp_path) / "f14.db"
    store = SessionStore(str(path))
    try:
        store._now = lambda: CREATED
        store.save_profile("爸爸", {})
        forged = {
            "visit_records": [
                {
                    "id": 10, "profile": "爸爸",
                    "visit_date": "2026-10-01", "diagnosis": "高血压",
                    "next_followup_date": _day(20),
                    "followup_appointment_id": 0,
                    "followup_reminder_id": 0,
                },
            ],
            "appointments": [
                {
                    "id": 30 + i, "profile": "爸爸",
                    "title": "复诊：高血压",
                    "scheduled_at": _day(20 + i * 4) + "T09:00",
                    "status": "upcoming", "owner_visit_id": 0,
                    "note": "由就诊记录 #10 自动创建",
                    "created_at": CREATED, "updated_at": CREATED,
                }
                for i in range(2)
            ],
            "reminders": [
                {
                    "id": 40 + i, "profile": "爸爸",
                    "title": "复诊：高血压",
                    "due_at": _day(19 + i * 4), "note": "复诊",
                    "appointment_id": 30 + i, "done": 0,
                    "created_at": CREATED, "updated_at": CREATED,
                }
                for i in range(2)
            ],
        }
        store.import_data(forged)
        assert store._conn.in_transaction is False

        def _owned(st):
            vid = int(st.list_visit_records("爸爸")[0]["id"])
            return [
                a
                for a in st.list_appointments("爸爸", None)
                if a["status"] == "upcoming"
                and (
                    int(a.get("owner_visit_id") or 0) == vid
                    or f"由就诊记录 #{vid} 自动创建"
                    in str(a.get("note") or "")
                )
            ]

        assert len(_owned(store)) == 1
        assert len(store.list_reminders(False, "爸爸")) == 1
    finally:
        store.close()
    reopened = SessionStore(str(path))
    try:
        vid = int(reopened.list_visit_records("爸爸")[0]["id"])
        owned = [
            a
            for a in reopened.list_appointments("爸爸", None)
            if a["status"] == "upcoming"
            and (
                int(a.get("owner_visit_id") or 0) == vid
                or f"由就诊记录 #{vid} 自动创建"
                in str(a.get("note") or "")
            )
        ]
        assert len(owned) == 1
        assert len(reopened.list_reminders(False, "爸爸")) == 1
    finally:
        reopened.close()


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "text",
    [
        "什么是胸痛，明天要去医院开吗丁啉",
        "胸痛是什么病，要去医院拿吗啡",
    ],
)
def test_urgent_question_marker_drug_names(text):
    # 吗丁啉/吗啡里的「吗」不是疑问标记（第一百一十四轮 [13]）
    assert _level(text) == "none", _red(text)


def test_urgent_question_marker_control():
    assert "胸痛" in _labels("胸痛是什么病，需要去医院检查一下吗")


# ---------------------------------------------------------------- [16]
def test_context_allergy_family_clause_ignored():
    # 档案过敏史里的家属分句不算患者过敏（第一百一十四轮 [16]）
    result = check_medication_safety(
        "我能吃阿莫西林吗",
        {"age": "62", "allergies": "我爸对青霉素过敏，我不过敏"},
    )
    assert not any("青霉素" in str(t) for t in _titles(result)), result
    result = check_medication_safety(
        "我能吃阿莫西林吗",
        {"age": "62", "allergies": "我对青霉素过敏"},
    )
    assert any("青霉素" in str(t) for t in _titles(result)), result


# ---------------------------------------------------------------- [17]
def test_web_detach_waits_for_queued_send():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("async function detachSessionForProfileChange()")
    block = source[start:source.index("\n}", start)]
    guard = block.find("if (sendInFlight)")
    busy = block.find("if (busy)")
    # 排队发送时程序化换档走静默分支，不弹「会诊进行中」
    # （第一百一十四轮 [17]）
    assert guard != -1 and busy != -1 and guard < busy
    assert "return false;" in block[guard:guard + 300]


# ---------------------------------------------------------------- [18]
@pytest.mark.parametrize(
    "text",
    ["榔头砸到脚趾了", "奶头被宝宝撞到", "奶头起了个包"],
)
def test_non_head_impact_nouns(text):
    # 榔头/奶头里的「头」不是头部外伤（第一百一十四轮 [18]）
    assert _level(text, WARFARIN) == "none", _red(text, WARFARIN)


def test_non_head_impact_control():
    assert _level("乳头被宝宝撞到", WARFARIN) == "none"
