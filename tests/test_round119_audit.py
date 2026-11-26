# -*- coding: utf-8 -*-
"""第一百一十九轮审查修复的回归测试。"""
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
    "text",
    [
        "网上说胸痛要重视我胸口痛",
        "网上说呕血要马上去医院我呕血了",
        "网上说颈部僵硬要急诊我颈部僵硬",
        "网上说胸痛要重视我这几天胸痛",
    ],
)
def test_quote_then_patient_red_flag_kept(text):
    # 引述词后同句出现患者本人红旗症状要保留（第一百一十九轮 [1]）
    assert _level(text) == "emergency", patient_complaint_text(text)


def test_quote_controls():
    assert _level("网上说胸痛要重视") == "none"
    assert "胸痛" in _labels("网上说胸痛要重视，我胸口痛")


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "华法林停了一年多，孩子出生后吃上了，今天牙疼能吃布洛芬吗",
        "华法林停了一年多，产后吃上了，今天牙疼能吃布洛芬吗",
    ],
)
def test_postpartum_restart_medication_safety(text):
    # 产后续用在用药通道同样生效（第一百一十九轮 [3]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [4]
def test_spaced_bracket_stop_attributed():
    # 「华法林 3mg（已停用两年）」的标注归因到前一药名
    # （第一百一十九轮 [4]）
    assert _level(
        "今天头撞到门框",
        {"age": "40岁", "medications": "华法林 3mg（已停用两年）"},
    ) == "none"
    assert _level(
        "今天发烧39度",
        {"age": "40岁", "medications": "泼尼松 5mg（已停用两年）"},
    ) == "none"
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框",
        {"age": "40岁", "medications": "华法林 3mg（每日一次）"},
    )


# ---------------------------------------------------------------- [5][11]
@pytest.mark.parametrize(
    "text",
    [
        "不知道自己有没有怀孕，现在没有出血",
        "不确定有没有怀孕，没有出血",
        "不确定是不是怀孕，下面没有出血",
        "有没有怀孕不知道，没有出血",
        "孕8周 不确定是不是羊水 阴道没有出血",
        "孕8周不知道是不是宫缩下面没有出血",
        "孕8周不确定有没有发烧下面没有出血",
    ],
)
def test_uncertain_not_bleeding_negated(text):
    # 不确定对象不是出血、或出血被明确否认时不报急症
    # （第一百一十九轮 [5][11]）
    assert _level(text) == "none", _red(text)


def test_uncertain_bleeding_control():
    assert "孕产期出血" in _labels("孕8周，不确定下面有没有出血")


# ---------------------------------------------------------------- [10]
@pytest.mark.parametrize(
    "text",
    [
        "华法林停了一年多，产后不敢再吃了，今天头撞到门框",
        "华法林停了一年多，后来生了病不能再吃了，今天头撞到门框",
        "华法林停了一年多，生完孩子后不能吃了，今天头撞到门框",
        "华法林停了一年多，孩子出生后吃奶粉，今天头撞到门框",
    ],
)
def test_postpartum_restart_negation_and_food(text):
    # 产后续用分支排除否定与食物宾语（第一百一十九轮 [10]）
    assert _level(text) == "none", _red(text)


def test_postpartum_restart_control():
    assert "抗凝治疗期间头部外伤" in _labels(
        "华法林停了一年多，孩子出生后吃上了，今天头撞到门框"
    )


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text",
    [
        "出现以下高危症状请立即就医：收缩压超过 180 mmHg、胸痛、意识改变。",
        "以下中危信号需要复诊。",
        "这些低危表现可观察。",
    ],
)
def test_risk_level_followed_by_symptom_nouns(text):
    # 高危症状/信号等对象后缀不是风险分级（第一百一十九轮 [8]）
    assert api_server.parse_risk_level(text) == ""


def test_risk_level_control():
    assert api_server.parse_risk_level("情况高危，请立即就医。") == "high"


# ---------------------------------------------------------------- [12]
def test_twelve_weeks_stop_not_two_weeks():
    # 12 周不能被当成 2 周近期停药（第一百一十九轮 [12]）
    result = check_medication_safety(
        "牙疼，能吃布洛芬吗", {"medications": "华法林（停药12周）"}
    )
    assert "华法林与非甾体抗炎药" not in _titles(result), result
    names = api_server._context_medication_names(
        {"medications": "华法林（停药12周）"}
    )
    assert not any("华法林" in n for n in names), names
    result = check_medication_safety(
        "牙疼，能吃布洛芬吗", {"medications": "华法林（停药2周）"}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [13][15]
def test_today_started_medication_not_warning():
    # 当天新开药 0% 不算依从 warning（第一百一十九轮 [13]）
    today = api_server.datetime.now().strftime("%Y-%m-%d")
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={
            "worst_rate": 0,
            "adherence": {"expected": 1, "rate": 0},
            "oldest_started_at": today + "T08:00:00",
        },
        medication_lab={}, vaccinations={}, appointments={},
    )
    assert attention.get("level") == "info", attention


def test_old_low_frequency_medication_warning():
    # 低频老药 0% 不能被 info 级用药安全提示压掉
    # （第一百一十九轮 [15]）
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={
            "worst_rate": 0,
            "adherence": {"expected": 1, "rate": 0},
            "oldest_started_at": "2026-06-10T08:00:00",
        },
        medication_lab={"count": 1, "level": "info",
                        "alerts": [{"title": "头孢类与酒精"}]},
        vaccinations={}, appointments={},
    )
    assert attention.get("level") == "warning"
    assert "用药打卡" in str(attention.get("label") or "")


# ---------------------------------------------------------------- [2]
def test_independent_twin_not_claimed(tmp_path):
    # 独立来源同秒孪生（仅文本编辑）不能被认领，删除后恢复
    # （第一百一十九轮 [2]）
    src_a = _store(tmp_path, "s2a")
    src_b = _store(tmp_path, "s2b")
    target = _store(tmp_path, "s2t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_reminder(
            "吃药", "2026-06-08", note="A说明", profile="妈妈"
        )
        backup_a = src_a.export_data()
        src_b.add_reminder(
            "吃药", "2026-06-08", note="B说明", profile="妈妈"
        )
        row_b = src_b.list_reminders(False, "妈妈")[0]
        src_b._now = lambda: "2026-06-02T08:00:00"
        src_b.update_reminder(int(row_b["id"]), note="B改")
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        target.import_data(backup_b)
        a_row = next(
            r for r in target.list_reminders(True, "妈妈")
            if r["note"] == "A说明"
        )
        target.delete_reminder(int(a_row["id"]))
        added = target.import_data(backup_a).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        assert added == 1
        assert any(r["note"] == "A说明" for r in rows)
        assert any(r["note"] == "B改" for r in rows)
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [6]
def test_slash_date_repeat_idempotent(tmp_path):
    # 非规范日期不能让标记型旧备份重复导入时重复插入
    # （第一百一十九轮 [6]）
    store = _store(tmp_path, "s6")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = {
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026/11/24",
                 "diagnosis": "高血压", "next_followup_date": "2026-12-16",
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-16T09:00", "status": "upcoming",
                 "owner_visit_id": 0,
                 "note": "由就诊记录 #10 自动创建"},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": "2026-12-15", "note": "复诊",
                 "appointment_id": 30, "done": 0},
            ],
        }
        store.import_data(forged)
        store.import_data(forged)
        assert len(store.list_appointments("妈妈", None)) == 1
        assert len(store.list_reminders(False, "妈妈")) == 1
    finally:
        store.close()


# ---------------------------------------------------------------- [7]
def test_marker_only_backup_repeat_idempotent(tmp_path):
    # 无就诊的标记型旧备份重复导入幂等（第一百一十九轮 [7]）
    store = _store(tmp_path, "s7")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = {
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-16T09:00", "status": "upcoming",
                 "owner_visit_id": 0,
                 "note": "由就诊记录 #10 自动创建"},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": "2026-12-15", "note": "复诊",
                 "appointment_id": 30, "done": 0},
            ],
        }
        for _ in range(3):
            store.import_data(forged)
        assert len(store.list_appointments("妈妈", None)) == 1
        assert len(store.list_reminders(False, "妈妈")) == 1
    finally:
        store.close()


# ---------------------------------------------------------------- [9][14]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_launch_wait_persists_inflight():
    source = _app_js()
    start = source.index("async function send(")
    block = source[start:start + 1400]
    persist_at = block.find("INFLIGHT_QUESTION_KEY")
    await_at = block.find("await Promise.race([")
    assert persist_at != -1 and await_at != -1
    assert persist_at < await_at


def test_web_deferred_draft_slot_and_consumer():
    source = _app_js()
    assert "medix.deferredDraft." in source
    assert "function consumeDeferredDraftForSession(" in source
    assert "consumeDeferredDraftForSession(id);" in source
