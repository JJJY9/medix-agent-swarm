# -*- coding: utf-8 -*-
"""第一百零六轮审查修复的回归测试。"""
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
    "text,context,label",
    [
        (
            "今天头撞到门框",
            {"medications": "华法林 3mg 每日一次、阿司匹林（已停用两年）"},
            "抗凝治疗期间头部外伤",
        ),
        (
            "今天头撞到门框",
            {"medications": "华法林 3mg 每日一次、阿司匹林（已停用 2 个月）"},
            "抗凝治疗期间头部外伤",
        ),
        (
            "今天发烧39度",
            {"medications": "甲氨蝶呤 每周一次、泼尼松（已停用两年）"},
            "免疫功能低下伴发热",
        ),
    ],
)
def test_mixed_active_and_bracketed_stop_keeps_active(text, context, label):
    # 同分句里在用药物 + 括号远期停药：只剔除被停药，在用药物仍
    # 触发急症（第一百零六轮 [1]）
    assert label in _labels(text, dict(context, age="55")), _red(
        text, dict(context, age="55")
    )


def test_mixed_active_control():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框", {"medications": "华法林 3mg 每日一次"}
    )
    # 只写被停药物时不触发
    assert _level(
        "今天头撞到门框", {"medications": "阿司匹林（已停用两年）"}
    ) == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "term",
    [
        "月经两个月没来",
        "大姨妈两个月没来",
        "例假迟了十天",
        "月经晚了十天",
        "月经已经两个月没来了",
        "月经两个多月没来",
    ],
)
def test_amenorrhea_quantity_word_order(term):
    assert "孕产期出血" in _labels("阴道出血", {"history": term}), term


def test_amenorrhea_controls():
    assert "孕产期出血" in _labels("月经两个月没来，今天阴道出血")
    assert "孕产期出血" in _labels("两个月没来月经了，今天阴道出血")
    assert _level("停经多年，阴道出血") == "none"
    assert "孕产期出血" in _labels("停经45天，阴道出血")


# ---------------------------------------------------------------- [3]
def test_soft_pool_fallback_skips_stale_rows(tmp_path):
    store = _store(tmp_path, "x3")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.import_data({"reminders": [
            {"id": 1, "profile": "妈妈", "title": "吃药", "due_at": _day(21),
             "note": "n2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复查", "due_at": _day(21),
             "note": "n2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]})
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "复查", "due_at": _day(21),
             "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "吃药", "due_at": _day(7),
             "note": "n1", "done": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = {
            (r["profile"], r["note"])
            for r in store.list_reminders(True, None)
        }
        # 软字段兜底不能把内容不符且未编辑的 stale 行认领掉备份行
        assert ("妈妈", "n1") in rows, rows
        assert store.import_data(backup).get("reminders") == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [4][14]
def test_shared_stop_keeps_continued_drug():
    names = api_server._context_medication_names
    for text in [
        "已停用阿司匹林、氯吡格雷仍在服用",
        "停用阿司匹林、氯吡格雷还在吃",
        "已停用阿司匹林、氯吡格雷尚未停用",
        "已停用阿司匹林、氯吡格雷没停",
    ]:
        got = names({"medications": text})
        assert any("氯吡格雷" in item for item in got), (text, got)
    got = names({"medications": "已停用阿司匹林、氯吡格雷仍在服用"})
    result = check_medication_safety(
        "、".join(got + ["奥美拉唑"]),
        {"medications": "、".join(got + ["奥美拉唑"])},
    )
    assert "氯吡格雷与奥美拉唑" in _titles(result), result
    got = names({"medications": "已停用阿司匹林、氯吡格雷继续服用"})
    assert got == ["氯吡格雷继续服用"], got


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize("age_ctx", [{"age": "8岁"}, {}])
def test_daughter_child_patient_keeps_anticoagulation(age_ctx):
    # 患儿/未知年龄档案下「我闺女」就是患者本人（第一百零六轮 [5]）
    assert "抗凝治疗期间头部外伤" in _labels(
        "我闺女在吃华法林，今天头撞到门框", dict(age_ctx)
    )
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框",
        dict(age_ctx, history="我闺女长期吃华法林"),
    )


def test_daughter_adult_control():
    assert _level(
        "我闺女在吃华法林，今天头撞到门框", {"age": "40岁"}
    ) == "none"


# ---------------------------------------------------------------- [6]
def test_recent_stop_de_form():
    result = check_medication_safety("华法林昨天停的，能吃布洛芬吗", {})
    assert "华法林与非甾体抗炎药" in _titles(result), result
    result = check_medication_safety("华法林前天停的，能吃布洛芬吗", {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_recent_stop_de_controls():
    result = check_medication_safety(
        "华法林昨天停的时间不长了，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning", result
    assert check_medication_safety(
        "华法林两年前停的，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "什么病会胸痛",
        "什么原因会胸痛",
        "什么情况下会咯血",
        "什么情况下会抽搐",
        "请问什么病会胸痛",
    ],
)
def test_definition_modal_question_is_educational(text):
    assert _level(text) == "none", _red(text)


def test_definition_modal_controls():
    assert _level("什么病会引起胸痛") == "none"
    assert "胸痛" in _labels("胸痛持续两天了是什么病")


# ---------------------------------------------------------------- [8]
def test_restored_twin_edit_reimport_idempotent(tmp_path):
    store = _store(tmp_path, "x8")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复查", "due_at": _day(10),
             "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊", "due_at": _day(10),
             "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = {
            r["title"]: int(r["id"])
            for r in store.list_reminders(False, "妈妈")
        }
        store.delete_reminder(rows["复查"])
        store.import_data(backup)
        restored = next(
            int(r["id"]) for r in store.list_reminders(False, "妈妈")
            if r["title"] == "复查"
        )
        store._now = lambda: LATER
        store.update_reminder(restored, note="EDITED")
        added = store.import_data(backup).get("reminders")
        final = sorted(
            (r["title"], r["note"])
            for r in store.list_reminders(False, "妈妈")
        )
        # 兄弟行已存活：恢复副本被编辑后导入不能再补插编辑前旧副本
        assert added == 0, final
        assert final == [("复查", "EDITED"), ("复诊", "n1")], final
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_mismatched_leftover_cancel_keeps_current_reminder(tmp_path):
    store = _store(tmp_path, "x9")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        d0, d1 = _day(5), _day(10)
        vid = int(store.add_visit_record(
            "妈妈", visit_date=_day(-3), diagnosis="高血压",
            next_followup_date=d0,
        )["id"])
        appt = int(store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d1 + "T09:00",
            owner_visit_id=vid,
        )["id"])
        reminder = int(store.add_reminder(
            "复诊：X", _day(4), note="复诊 " + d0, profile="妈妈",
        )["id"])
        store.set_visit_followup_links(vid, 0, reminder)
        store._now = lambda: LATER
        store.update_appointment_partial(appt, status="cancelled")
        # 预约日期与就诊当前复诊日期不一致：不是「当前计划」，
        # 取消它不能删就诊的脱钩复诊提醒（第一百零六轮 [9]）
        assert store.get_reminder(reminder) is not None
        assert int(
            store.get_visit_record(vid)["followup_reminder_id"] or 0
        ) == reminder
        store.delete_appointment(appt)
        assert store.get_reminder(reminder) is not None
    finally:
        store.close()


def test_aligned_leftover_cancel_cleans_reminder(tmp_path):
    store = _store(tmp_path, "x9b")
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
            owner_visit_id=vid,
        )["id"])
        reminder = int(store.add_reminder(
            "复诊：X", _day(4), note="复诊 " + d0, profile="妈妈",
        )["id"])
        store.set_visit_followup_links(vid, 0, reminder)
        store._now = lambda: LATER
        store.update_appointment_partial(appt, status="cancelled")
        # 日期一致时仍按第一百零二轮 [9] 清理
        assert store.get_reminder(reminder) is None
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
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


def test_proxy_blank_trailing_line_fails_closed(proxy_env):
    request = _proxy_request({"x-real-ip": ["198.51.100.10", ""]})
    assert api_server._forwarded_appended_address(request) == ""
    assert api_server._forwarded_client_address(request) == ""
    request = _proxy_request({"x-real-ip": ["198.51.100.10", "   "]})
    assert api_server._forwarded_appended_address(request) == ""


def test_proxy_trailing_line_controls(proxy_env):
    request = _proxy_request(
        {"x-real-ip": ["198.51.100.10", "203.0.113.77"]}
    )
    assert api_server._forwarded_appended_address(request).startswith(
        "203.0.113"
    )
    proxy_env.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    request = _proxy_request({"x-forwarded-for": ["198.51.100.10, "]})
    assert api_server._forwarded_appended_address(request) == ""


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    [
        "阿司匹林、布洛芬都停了",
        "阿司匹林、布洛芬已经停用",
        "阿司匹林、布洛芬都已停用",
        "停了阿司匹林、布洛芬",
        "停止服用阿司匹林、布洛芬",
    ],
)
def test_shared_stop_suffix_forms(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == [], text


def test_shared_stop_suffix_controls():
    names = api_server._context_medication_names
    assert names({"medications": "华法林、阿司匹林 两个月前已停用"}) == [
        "华法林"
    ]
    assert names({"medications": "华法林、阿司匹林（已停用）"}) == [
        "华法林"
    ]
    assert names({"medications": "已停用阿司匹林、氯吡格雷"}) == []


# ---------------------------------------------------------------- [12]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_failure_branch_preserves_backup_slot_source():
    source = _app_js()
    # 中止与通用失败分支只在不处于备份槽来源时改写 keyName
    assert source.count(
        "if (inflightRestoredKeyName !== INFLIGHT_QUESTION_OTHER_KEY) {"
    ) >= 2
    # 主槽来源的清理路径同时清除备份槽同会话同文本记录
    start = source.index("function clearRestoredInflightIfOwned()")
    block = source[start:start + 900]
    assert "removeOtherInflightRecord(restoredOwner, inflightRestoredText);" in block
    start = source.index(
        "} else if (\n        !restoredOwner || restoredOwner === (sessionId || '')"
    )
    block = source[start:start + 700]
    assert "removeOtherInflightRecord(restoredOwner, inflightRestoredText);" in block


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize("obj", ["这药", "该药", "此药", "一样的药"])
def test_triage_restart_self_pronouns(obj):
    assert "抗凝治疗期间头部外伤" in _labels(
        f"华法林停了半年，现在在吃{obj}，今天头撞到门框"
    ), obj


def test_triage_restart_pronoun_controls():
    assert "抗凝治疗期间头部外伤" in _labels(
        "华法林停了半年，现在在吃这个药，今天头撞到门框"
    )
    # 非药物对象不复活被停药
    assert "抗凝治疗期间头部外伤" not in _labels(
        "华法林停了半年，现在在吃火锅，今天头撞到门框"
    )


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize(
    "text",
    [
        "请解释一下我外甥抽搐是什么病",
        "介绍一下我孙子抽搐是什么病",
        "请解释一下我叔叔胸痛是什么病",
        "解释一下我外甥抽搐",
    ],
)
def test_definition_prefix_kin_family(text):
    matches = _labels(text)
    assert "抽搐" in matches or "胸痛" in matches, _red(text)


def test_definition_prefix_kin_control():
    assert "抽搐" in _labels("请解释一下我爸抽搐是什么病")
