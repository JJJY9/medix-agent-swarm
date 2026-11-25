# -*- coding: utf-8 -*-
"""第一百零八轮审查修复的回归测试。"""
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
        "摔了一跤，后脑勺流血了",
        "摔倒后头顶流血了",
        "摔倒了后脑勺出血了",
        "摔了一跤，后脑勺缝了三针",
        "后脑勺流血不止",
        "头顶出血了",
        "太阳穴流血了",
    ],
)
def test_head_trauma_bleeding_variants(text):
    # 部位词直接跟出血/缝针同样是头部外伤（第一百零八轮 [1]）
    assert "抗凝治疗期间头部外伤" in _labels(text, WARFARIN), text


def test_head_trauma_bleeding_controls():
    assert _level("摔了一跤，膝盖擦破了", WARFARIN) == "none"
    assert "抗凝治疗期间头部外伤" in _labels(
        "摔了一跤，后脑勺磕破了", WARFARIN
    )


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病，现在还疼，需要去医院吗",
        "胸痛是什么病，现在还疼，要不要紧",
        "胸痛是什么病，现在还在痛，需要去医院吗",
        "胸痛是什么病，仍然疼，需要去医院吗",
        "胸痛是什么病，现在还疼，疼得冒冷汗，需要去医院吗",
    ],
)
def test_definition_lead_cross_clause_help(text):
    # 求助/病程线索可以隔着中间分句；「还疼/还在痛」是当前病程
    # （第一百零八轮 [2]）
    assert "胸痛" in _labels(text), _red(text)


def test_definition_lead_cross_clause_controls():
    assert "呼吸困难" in _labels("呼吸困难是什么病，现在还喘")
    assert _level("什么是胸痛") == "none"
    assert _level("胸痛是什么病，网上说这种病很严重") == "none"


# ---------------------------------------------------------------- [3]
def test_colliding_row_not_edit_evidence(tmp_path):
    store = _store(tmp_path, "x3")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.import_data({"reminders": [
            {"id": 1, "profile": "妈妈", "title": "无关1", "due_at": _day(9),
             "note": "", "done": 0,
             "created_at": "2026-05-01T07:00:00",
             "updated_at": "2026-05-01T07:00:00"},
            {"id": 2, "profile": "妈妈", "title": "无关2", "due_at": _day(11),
             "note": "", "done": 0,
             "created_at": "2026-05-01T07:00:00",
             "updated_at": "2026-11-05T10:00:00"},
            {"id": 4, "profile": "爸爸", "title": "复查", "due_at": _day(19),
             "note": "n2", "advance_days": 0, "repeat_days": 7, "done": 0,
             "created_at": "2026-06-01T08:00:01",
             "updated_at": "2026-06-01T08:00:01"},
        ]})
        backup = {
            "exported_at": "2026-11-01T10:00:00",
            "reminders": [
                {"id": 1, "profile": "妈妈", "title": "占位",
                 "due_at": _day(13), "note": "", "done": 0,
                 "created_at": "2026-06-01T08:00:01",
                 "updated_at": "2026-06-01T08:00:01"},
                {"id": 2, "profile": "爸爸", "title": "吃药",
                 "due_at": _day(19), "note": "n2", "advance_days": 0,
                 "repeat_days": 7, "done": 0,
                 "created_at": "2026-06-01T08:00:01",
                 "updated_at": "2026-06-01T08:00:01"},
            ],
        }
        added = store.import_data(backup).get("reminders")
        titles = sorted(r["title"] for r in store.list_reminders(True, None))
        # 撞号无关行的编辑时间不能当作候选行的编辑证据
        assert added == 2, titles
        assert "吃药" in titles, titles
        assert store.import_data(backup).get("reminders") == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text,expect",
    [
        ("华法林每天吃、阿司匹林停了", ["华法林每天吃"]),
        ("已停用阿司匹林、华法林每天吃", ["华法林每天吃"]),
        ("华法林每日服用、阿司匹林停了", ["华法林每日服用"]),
    ],
)
def test_frequency_use_not_shared_stop(text, expect):
    # 日常服药频率说明该项仍在用，不参与共享停药（第一百零八轮 [4]）
    assert api_server._context_medication_names(
        {"medications": text}
    ) == expect, text


def test_frequency_use_controls():
    assert api_server._context_medication_names(
        {"medications": "华法林、阿司匹林都停了两年"}
    ) == []


# ---------------------------------------------------------------- [5][13]
@pytest.mark.parametrize(
    "text",
    [
        "华法林和阿司匹林都停用吗？我现在能吃布洛芬吗",
        "华法林和阿司匹林都停用了吗？我现在能吃布洛芬吗",
        "华法林、阿司匹林都停了可以吗，能吃布洛芬吗",
        "华法林、阿司匹林都停了是否安全，能吃布洛芬吗",
    ],
)
def test_list_shared_stop_question_tail(text):
    # 疑问尾是意向询问，按仍在用药处理（第一百零八轮 [5][13]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_list_shared_stop_question_controls():
    result = check_medication_safety(
        "华法林和阿司匹林都停了两天，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result)
    result = check_medication_safety(
        "华法林和阿司匹林都停了两年，能吃布洛芬吗", {}
    )
    assert result["level"] == "none"


# ---------------------------------------------------------------- [6]
def test_fever_enumeration_negation():
    assert _level("宝宝发烧、咳嗽、胸痛都没有", {"age": "2个月"}) == "none"
    assert _level("发烧、咳嗽、胸痛都没有", {"history": "正在化疗"}) == "none"


def test_fever_enumeration_controls():
    assert "3 个月以下婴儿发热" in _labels(
        "宝宝发烧、咳嗽都很厉害", {"age": "2个月"}
    )
    assert _level("宝宝发烧和咳嗽都没有", {"age": "2个月"}) == "none"


# ---------------------------------------------------------------- [7]
def test_moved_sibling_idempotent(tmp_path):
    store = _store(tmp_path, "x7")
    store._now = lambda: CREATED
    try:
        for name in ("妈妈", "爸爸", "奶奶"):
            store.save_profile(name, {})
        store.import_data({"reminders": [
            {"id": 1, "profile": "妈妈", "title": "吃药", "due_at": _day(19),
             "note": "n1", "done": 0,
             "created_at": "2026-06-01T08:00:00",
             "updated_at": "2026-06-01T08:00:00"},
            {"id": 2, "profile": "爸爸", "title": "吃药", "due_at": _day(19),
             "note": "n2", "done": 0,
             "created_at": "2026-06-01T08:00:00",
             "updated_at": "2026-06-01T08:00:00"},
            {"id": 3, "profile": "妈妈", "title": "吃药", "due_at": _day(19),
             "note": "n1", "done": 0,
             "created_at": "2026-06-01T08:00:00",
             "updated_at": "2026-06-01T08:00:00"},
            {"id": 4, "profile": "爸爸", "title": "吃药", "due_at": _day(19),
             "note": "n2", "done": 0,
             "created_at": "2026-06-01T08:00:00",
             "updated_at": "2026-06-01T08:00:00"},
            {"id": 5, "profile": "爸爸", "title": "吃药", "due_at": _day(19),
             "note": "n3", "done": 0,
             "created_at": "2026-06-01T08:00:00",
             "updated_at": "2026-06-01T08:00:00"},
        ]})
        backup = store.export_data()
        store._now = lambda: "2026-10-01T09:00:00"
        store.update_reminder(3, profile="奶奶")
        store.update_reminder(4, profile="妈妈")
        store.delete_reminder(2)
        store.delete_reminder(5)
        results = [
            store.import_data(backup).get("reminders") for _ in range(3)
        ]
        n2_rows = [
            r for r in store.list_reminders(True, None)
            if r["note"] == "n2" and r["profile"] == "爸爸"
        ]
        # 兄弟行被改挂到第三档案后仍算存活：重复导入保持幂等，
        # 不再复制已恢复的 爸爸/n2 提醒（第一百零八轮 [7]）
        assert results == [2, 0, 0], results
        assert len(n2_rows) == 1, n2_rows
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def test_cancel_with_reschedule_cleans_reminder(tmp_path):
    d1, d2 = _day(20), _day(26)
    store = _store(tmp_path, "x8")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.import_data({
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-10-01",
                 "diagnosis": "高血压", "next_followup_date": d1,
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 20},
            ],
            "reminders": [
                {"id": 20, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": _day(19), "note": "复诊 " + d1,
                 "appointment_id": 0, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": d1 + "T09:00", "status": "upcoming",
                 "owner_visit_id": 10,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        })
        vid = int(store.list_visit_records("妈妈")[0]["id"])
        aid = int(store.list_appointments("妈妈", None)[0]["id"])
        store.update_appointment_partial(
            aid, status="cancelled", scheduled_at=d2 + "T10:00",
        )
        reminders = store.list_reminders(True, None)
        visit = store.get_visit_record(vid)
        # 同一 PATCH 取消+改期时按取消前日期判断「当前计划」，
        # 脱钩复诊提醒必须清理（第一百零八轮 [8]）
        assert reminders == [], reminders
        assert int(visit["followup_reminder_id"] or 0) == 0, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_restore_link_failure_cuts_visit_link(tmp_path):
    d1, d2 = _day(20), _day(26)
    store = _store(tmp_path, "x9")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        visit = store.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=d1,
        )
        vid = int(visit["id"])
        appt = store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=d1 + "T09:00",
        )
        aid = int(appt["id"])
        store.set_visit_followup_links(vid, aid, 0)
        store.update_appointment_partial(aid, status="completed")
        store._now = lambda: "2026-06-03T08:00:00"
        store.update_appointment_partial(
            aid, status="upcoming", scheduled_at=d2 + "T10:00",
        )
        visit = store.get_visit_record(vid)
        # 链接回写失败（日期变更）必须切断就诊旧链接，避免逾期
        # 提示被抑制与重复预约（第一百零八轮 [9]）
        assert int(visit["followup_appointment_id"] or 0) == 0, visit
        out = api_server._schedule_followup_sync(store, vid)
        assert out.get("appointment_created") is True
        upcoming = [
            a for a in store.list_appointments("妈妈", None)
            if a["status"] == "upcoming"
            and int(a.get("owner_visit_id") or 0) == vid
        ]
        assert len(upcoming) == 1, upcoming
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
class _Headers:
    def __init__(self, mapping):
        self._m = {
            key.lower(): (value if isinstance(value, list) else [value])
            for key, value in mapping.items()
        }

    def getlist(self, name):
        return list(self._m.get(name.lower(), []))

    def get(self, name, default=None):
        values = self._m.get(name.lower())
        return values[0] if values else default

    def items(self):
        return [(key, values[0]) for key, values in self._m.items()]


class _Req:
    def __init__(self, mapping):
        self.headers = _Headers(mapping)
        self.client = type("C", (), {"host": "127.0.0.1"})()


@pytest.fixture
def proxy_env(monkeypatch):
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    yield monkeypatch


def test_blank_address_header_fails_closed(proxy_env):
    request = _Req({"x-forwarded-for": [""]})
    # 空白地址头是「地址不可得」：必须按远程 fail closed，不能
    # 当作本机直连（第一百零八轮 [10]）
    assert api_server._has_proxy_headers(request) is True
    assert api_server._has_forwarded_address_header(request) is True
    assert api_server._forwarded_client_address(request) == ""
    assert api_server._is_loopback_client(request) is False


def test_blank_address_header_controls(proxy_env):
    request = _Req({"x-forwarded-proto": [""]})
    assert api_server._has_proxy_headers(request) is False
    request = _Req({"x-forwarded-for": ["198.51.100.10", "203.0.113.77"]})
    assert api_server._forwarded_appended_address(request).startswith(
        "203.0.113"
    )


# ---------------------------------------------------------------- [11]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_persisted_marker_uses_record_owner():
    source = _app_js()
    start = source.index("function restoreInflightForOpenSession")
    end = source.index("function restoreInflightQuestion", start)
    block = source[start:end]
    persisted = block[block.index("if (persisted) {"):]
    persisted = persisted[:persisted.index("removeInflightRecord(record);")]
    # 启动路径先清空 inflightRestored*：守卫必须接受记录自身 owner
    # （第一百零八轮 [11]）
    assert "owner === targetId" in persisted, persisted
    guard = persisted.index("inflightInputMatchesRestored(question)")
    marker = persisted.index("notePersistedInflight(question, targetId);")
    assert guard < marker, persisted


# ---------------------------------------------------------------- [12]
def test_group_stop_clears_only_current_sentence():
    names = api_server._context_medication_names
    assert names(
        {"medications": "华法林 3mg 每日一次；阿司匹林、氯吡格雷，都停了"}
    ) == ["华法林 3mg 每日一次"]
    assert names(
        {
            "medications": "二甲双胍 500mg 每日两次",
            "active_medications": "阿司匹林、氯吡格雷，都停了",
        }
    ) == ["二甲双胍 500mg 每日两次"]


def test_group_stop_clears_current_sentence_controls():
    names = api_server._context_medication_names
    assert names({"medications": "华法林、阿司匹林，都停了"}) == []
    assert names({"medications": "华法林、阿司匹林，都停了两年"}) == []


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林，都停了两年",
        "华法林、阿司匹林，都已经停了两年",
        "华法林、阿司匹林，都停药很久了",
        "华法林、阿司匹林，均停用了两年",
    ],
)
def test_comma_group_stop_with_duration(text):
    # 逗号后群标记+时长作用于整句枚举（第一百零八轮 [14][16]）
    assert api_server._context_medication_names(
        {"medications": text}
    ) == [], text


def test_comma_group_stop_with_duration_controls():
    names = api_server._context_medication_names
    assert names(
        {"medications": "华法林 3mg 每日一次；阿司匹林、氯吡格雷，都停了两年"}
    ) == ["华法林 3mg 每日一次"]
    assert names({"medications": "华法林、阿司匹林，都停了"}) == []


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize(
    "text",
    ["华法林没吃了，能吃布洛芬吗", "华法林已经没吃了，能吃布洛芬吗"],
)
def test_mei_chi_le_suffix_stop(text):
    result = check_medication_safety(text, {})
    assert result["level"] == "none", result


def test_mei_chi_le_controls():
    result = check_medication_safety("华法林没吃完，能吃布洛芬吗", {})
    assert "华法林与非甾体抗炎药" in _titles(result)
    result = check_medication_safety("华法林没吃吗，能吃布洛芬吗", {})
    assert "华法林与非甾体抗炎药" in _titles(result)


# ---------------------------------------------------------------- [16]
def test_med_channel_comma_list_duration():
    result = check_medication_safety(
        "华法林、阿司匹林，都停了两年，能吃布洛芬吗", {}
    )
    assert result["level"] == "none", result


def test_med_channel_comma_list_duration_controls():
    result = check_medication_safety(
        "华法林在用，阿司匹林都停了两年，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result
    assert _level(
        "今天头撞到门框",
        {"medications": "华法林、阿司匹林，都停了两年"},
    ) == "none"
