# -*- coding: utf-8 -*-
"""第一百零七轮审查修复的回归测试。"""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"
LATER = "2026-11-02T09:00:00"


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


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "term",
    [
        "华法林3mg每天，阿司匹林是去年停的",
        "华法林3mg每天，去年停的阿司匹林",
    ],
)
def test_stop_de_attribution_triage(term):
    # 「停的」是他药停药的归属证据，在用华法林不能因此被判已停
    # （第一百零七轮 [1]）
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框", {"history": term}
    ), term


def test_stop_de_attribution_cross_clause():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框",
        {"medications": "华法林，阿司匹林（已停用两年）"},
    )


def test_stop_de_attribution_controls():
    assert _level(
        "今天头撞到门框", {"history": "华法林是去年停的"}
    ) == "none"
    assert _level(
        "今天头撞到门框", {"history": "华法林3mg每天，去年停了"}
    ) == "none"
    result = check_medication_safety(
        "我每天吃华法林，去年停的阿司匹林，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result
    result = check_medication_safety(
        "我每天吃华法林，阿司匹林是去年停的，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "胸口一阵阵的疼",
        "胸口隐隐的疼",
        "胸口针扎一样的疼",
        "胸部撕裂样的疼痛",
    ],
)
def test_chest_pain_de_like_modifiers(text):
    assert "胸痛" in _labels(text), text


def test_chest_pain_de_controls():
    assert "胸痛" in _labels("胸口一阵阵痛")
    assert _level("胸口压迫感没有了") == "none"


# ---------------------------------------------------------------- [3]
def test_reminder_title_mismatch_preserves_backup(tmp_path):
    store = _store(tmp_path, "x3")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        store.import_data({"reminders": [
            {"id": 1, "profile": "爸爸", "title": "测血压", "due_at": _day(21),
             "note": "n2", "advance_days": 1, "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]})
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "吃药", "due_at": _day(21),
             "note": "n2", "advance_days": 1, "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        added = store.import_data(backup).get("reminders")
        titles = sorted(r["title"] for r in store.list_reminders(False, "爸爸"))
        # 软字段不命中且无编辑证据：按被删记录补插，不能吞掉备份提醒
        assert added == 1, titles
        assert "吃药" in titles, titles
        assert store.import_data(backup).get("reminders") == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [4][5]
def test_reschedule_mismatched_leftover_keeps_visit_plan(tmp_path):
    store = _store(tmp_path, "x4")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        d0, d1 = _day(5), _day(21)
        vid = int(store.add_visit_record(
            "妈妈", visit_date=_day(-3), diagnosis="高血压",
            next_followup_date=d0,
        )["id"])
        rid = int(store.add_reminder(
            "复诊：X", _day(4), note="复诊 " + d0, profile="妈妈",
        )["id"])
        store.set_visit_followup_links(vid, 0, rid)
        appt = int(store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d1 + "T09:00",
            note=f"由就诊记录 #{vid} 自动创建",
        )["id"])
        store._now = lambda: LATER
        store.update_appointment_partial(appt, scheduled_at=d1 + "T15:00")
        visit = store.get_visit_record(vid)
        reminder = store.get_reminder(rid)
        # 时间-only 改期同样不能把就诊复诊日/提醒改到遗留预约日期
        assert str(visit["next_followup_date"])[:10] == d0, visit
        assert reminder is not None
        assert str(reminder["due_at"])[:10] == _day(4), reminder
        assert int(visit["followup_appointment_id"] or 0) == 0
        store.update_appointment_partial(appt, scheduled_at=_day(11) + "T10:00")
        visit = store.get_visit_record(vid)
        reminder = store.get_reminder(rid)
        assert str(visit["next_followup_date"])[:10] == d0, visit
        assert reminder is not None
        assert str(reminder["due_at"])[:10] == _day(4), reminder
        assert int(reminder["appointment_id"] or 0) == 0
    finally:
        store.close()


def test_reschedule_aligned_leftover_still_self_heals(tmp_path):
    store = _store(tmp_path, "x4b")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        d0 = _day(5)
        vid = int(store.add_visit_record(
            "妈妈", visit_date=_day(-3), diagnosis="高血压",
            next_followup_date=d0,
        )["id"])
        appt = int(store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d0 + "T09:00",
            note=f"由就诊记录 #{vid} 自动创建",
        )["id"])
        store._now = lambda: LATER
        store.update_appointment_partial(appt, scheduled_at=_day(8) + "T09:00")
        visit = store.get_visit_record(vid)
        # 改期前日期与就诊复诊日一致：仍按第一百轮 [9] 回写自愈
        assert str(visit["next_followup_date"])[:10] == _day(8), visit
        assert int(visit["followup_appointment_id"] or 0) == appt
    finally:
        store.close()


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "meds",
    [
        "华法林和阿司匹林（已停用两年）",
        "华法林与阿司匹林（已停用两年）",
        "华法林 阿司匹林（已停用两年）",
        "阿司匹林（已停用两年）和华法林",
        "华法林 3mg 每日一次和阿司匹林（已停用两年）",
    ],
)
def test_mixed_stop_clause_connectors(meds):
    # 和/与/空格连接的混合分句同样只剔除被停药项（第一百零七轮 [6]）
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框", {"medications": meds}
    ), meds


def test_mixed_stop_clause_controls():
    assert _level(
        "今天头撞到门框", {"medications": "阿司匹林（已停用两年）"}
    ) == "none"
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框",
        {"medications": "华法林、阿司匹林 两个月前已停用"},
    )


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "经期两个月没来，阴道出血",
        "经期推迟，今天见红",
        "生理期推迟了十天，阴道出血",
        "经期没来，阴道出血",
    ],
)
def test_amenorrhea_terms_jingqi(text):
    assert "孕产期出血" in _labels(text), text


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text",
    [
        "什么病会引发胸痛",
        "什么疾病会胸痛",
        "什么病引发胸痛",
        "哪类病会胸痛",
        "什么疾病会引起胸痛",
    ],
)
def test_definition_lead_variants(text):
    assert _level(text) == "none", _red(text)


def test_definition_lead_control():
    assert "胸痛" in _labels("胸痛持续两天了是什么病")


# ---------------------------------------------------------------- [9]
def test_deferred_claim_keeps_tail_fields(tmp_path):
    store = _store(tmp_path, "x9")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.import_data({"reminders": [
            {"id": 1, "profile": "爸爸", "title": "吃药", "due_at": _day(7),
             "note": "x2", "done": 0,
             "created_at": "2026-06-01T08:00:01",
             "updated_at": "2026-06-01T08:00:01"},
            {"id": 2, "profile": "妈妈", "title": "吃药", "due_at": _day(19),
             "note": "x1", "advance_days": 1, "repeat_days": 7, "done": 0,
             "created_at": "2026-06-01T08:00:01",
             "updated_at": "2026-06-01T08:00:01"},
        ]})
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "吃药", "due_at": _day(19),
             "note": "n1", "advance_days": 1, "repeat_days": 7, "done": 0,
             "created_at": "2026-06-01T08:00:01",
             "updated_at": "2026-06-01T08:00:01"},
            {"id": 2, "profile": "妈妈", "title": "复查", "due_at": _day(19),
             "note": "n1", "repeat_days": 7, "done": 0,
             "created_at": "2026-06-01T08:00:01",
             "updated_at": "2026-06-01T08:00:01"},
        ]}
        first = store.import_data(backup).get("reminders")
        notes = sorted(r["note"] for r in store.list_reminders(False, "妈妈"))
        # deferred 认领不能吞掉备份行的 note（组内缺行 + 未编辑）
        assert first == 2, notes
        assert notes == ["n1", "n1", "x1"], notes
        assert store.import_data(backup).get("reminders") == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_delete_then_reimport_no_twin_copies(tmp_path):
    store = _store(tmp_path, "x10")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.import_data({"reminders": [
            {"id": 9, "profile": "爸爸", "title": "复查", "due_at": _day(21),
             "note": "x1", "advance_days": 1, "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]})
        backup_b = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复查", "due_at": _day(19),
             "note": "", "advance_days": 1, "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复查", "due_at": _day(19),
             "note": "n2", "repeat_days": 7, "done": 0,
             "created_at": CREATED, "updated_at": LATER},
            {"id": 3, "profile": "妈妈", "title": "复查", "due_at": _day(7),
             "note": "", "advance_days": 1, "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 4, "profile": "爸爸", "title": "吃药", "due_at": _day(7),
             "note": "n2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup_b)
        rows = store.list_reminders(True, None)
        target = next(
            r for r in rows
            if r["profile"] == "爸爸" and r["title"] == "吃药"
        )
        store._now = lambda: LATER
        store.delete_reminder(int(target["id"]))
        before = len(store.list_reminders(True, None))
        added = store.import_data(backup_b).get("reminders")
        after = len(store.list_reminders(True, None))
        # 删除一条后重导入只补回被删行，两条存活同秒提醒不再各复制一份
        assert added == 1, (added, before, after)
        assert after == before + 1, (before, after)
        assert store.import_data(backup_b).get("reminders") == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def _proxy_request(mapping):
    class _Headers:
        def __init__(self, values):
            self._m = {
                key.lower(): (
                    value if isinstance(value, list) else [value]
                )
                for key, value in values.items()
            }

        def getlist(self, name):
            return list(self._m.get(name.lower(), []))

        def get(self, name, default=None):
            values = self._m.get(name.lower())
            return values[0] if values else default

    class _Req:
        def __init__(self, values):
            self.headers = _Headers(values)

    return _Req(mapping)


@pytest.fixture
def proxy_env(monkeypatch):
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-real-ip")
    yield monkeypatch


@pytest.mark.parametrize("tail", ["unknown", "unix:"])
def test_proxy_unparseable_trailing_line_fails_closed(proxy_env, tail):
    request = _proxy_request({"x-real-ip": ["127.0.0.1", tail]})
    # 代理追加行不可解析：不能回退到客户端自带的更早行
    assert api_server._forwarded_client_address(request) == ""
    assert api_server._forwarded_appended_address(request) == ""


def test_proxy_unparseable_trailing_controls(proxy_env):
    request = _proxy_request({"x-real-ip": ["127.0.0.1", ""]})
    assert api_server._forwarded_client_address(request) == ""
    request = _proxy_request(
        {"x-real-ip": ["198.51.100.10", "203.0.113.77"]}
    )
    assert api_server._forwarded_appended_address(request).startswith(
        "203.0.113"
    )


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text,expect",
    [
        ("华法林 3mg 每日一次，已停用两个月、布洛芬", ["布洛芬"]),
        ("华法林 3mg 每日一次 已停用两个月、布洛芬", ["布洛芬"]),
        ("氯吡格雷，已停用阿司匹林两个月、布洛芬", ["氯吡格雷"]),
    ],
)
def test_duration_enumeration_not_shared_stop(text, expect):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == expect, text


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林，都停了",
        "华法林、阿司匹林，均停了",
        "华法林、阿司匹林，全都停止了",
        "华法林、阿司匹林，也停了",
    ],
)
def test_comma_group_stop_clears_enumeration(text):
    assert api_server._context_medication_names({"medications": text}) == [], text


def test_comma_group_stop_control():
    assert api_server._context_medication_names(
        {"medications": "华法林、阿司匹林 两个月前已停用"}
    ) == ["华法林"]


# ---------------------------------------------------------------- [14][15]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_other_slot_cleanup_clears_main_slot():
    source = _app_js()
    # clearRestoredInflightIfOwned 的备份槽分支
    start = source.index("function clearRestoredInflightIfOwned()")
    block = source[start:start + 1100]
    first = block.index("removeOtherInflightRecord(restoredOwner, inflightRestoredText);")
    assert "clearInflightForCurrentSession();" in block[first:first + 300], block
    # 输入监听器的备份槽分支
    anchor = source.index(
        "// 备份槽记录按会话+文本清除（第九十七轮 [16]）"
    )
    block = source[anchor:anchor + 800]
    first = block.index("removeOtherInflightRecord(restoredOwner, inflightRestoredText);")
    assert "clearInflightForCurrentSession();" in block[first:first + 300], block


def test_web_persisted_marker_only_when_stripping():
    source = _app_js()
    start = source.index("function restoreInflightForOpenSession")
    end = source.index("function restoreInflightQuestion", start)
    block = source[start:end]
    persisted = block[block.index("if (persisted) {"):]
    persisted = persisted[:persisted.index("removeInflightRecord(record);")]
    guard = persisted.index("inflightInputMatchesRestored(question)")
    marker = persisted.index("notePersistedInflight(question, targetId);")
    # 只有确从输入框剥离时才武装已落库标记（第一百零七轮 [15]）
    assert guard < marker, persisted


# ---------------------------------------------------------------- [16]
def test_menstrual_nickname_not_family_subject():
    assert _level(
        "今天发烧39度", {"history": "大姨妈在化疗"}
    ) == "none"
    assert _level(
        "今天头撞到门框", {"history": "大姨妈在吃华法林"}
    ) == "none"
    assert "孕产期出血" in _labels(
        "阴道出血", {"history": "大姨妈两个月没来"}
    )


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "meds",
    [
        "华法林、阿司匹林都停了两年",
        "华法林、阿司匹林均已停用两年",
        "华法林、阿司匹林都停药两年了",
    ],
)
def test_shared_stop_with_duration_not_active(meds):
    # 共享停药作用于整个列表：分诊不报假急症
    assert _level("今天头撞到门框", {"medications": meds}) == "none", meds
    assert api_server._context_medication_names(
        {"medications": meds}
    ) == [], meds
    result = check_medication_safety(meds + "，能吃布洛芬吗", {})
    assert result["level"] == "none", result


def test_shared_stop_with_duration_controls():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框",
        {"medications": "华法林、阿司匹林 两个月前已停用"},
    )
    result = check_medication_safety(
        "华法林每天吃，阿司匹林都停了两年，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result)
