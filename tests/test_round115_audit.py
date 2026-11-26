# -*- coding: utf-8 -*-
"""第一百一十五轮审查修复的回归测试。"""
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
        "胸痛是什么病，要不要看医生",
        "胸痛是什么病，要不要送医院",
        "胸痛是什么病，要不要叫救护车",
        "咯血是什么病，要不要打120",
        "抽搐是什么病，要不要送医院",
    ],
)
def test_help_carriers_keep_definition_symptom(text):
    # 看医生/送医院/叫救护车/打120 都算求助语，定义问句前的急症
    # 主诉不能被整句滤掉（第一百一十五轮 [1]）
    assert _level(text) == "emergency", patient_complaint_text(text)
    assert _labels(text)


def test_help_carriers_controls():
    assert _level("胸痛是什么病，要不要去医院") == "emergency"
    assert _level("胸痛是什么病，需要看医生吗") == "emergency"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "华法林停了一年多，现在每天吃，今天头撞到门框了",
        "华法林停了一年多，最近每天吃，今天头撞到门框了",
    ],
)
def test_restart_daily_intake_keeps_anticoagulant(text):
    # 「现在每天吃」是续用，抗凝+头外伤不能漏报（第一百一十五轮 [2]）
    assert "抗凝治疗期间头部外伤" in _labels(text), _red(text)


def test_restart_daily_intake_context():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框了",
        {"history": "华法林停了一年多，现在每天吃", "age": "60岁"},
    )


def test_restart_medication_safety():
    for text in [
        "华法林停了一年多，上个月开始吃了，能吃布洛芬吗",
        "华法林停了一年多，现在每天吃，能吃布洛芬吗",
    ]:
        result = check_medication_safety(text, {})
        assert "华法林与非甾体抗炎药" in _titles(result), result


def test_restart_control():
    assert "抗凝治疗期间头部外伤" in _labels(
        "华法林停了一年多，最近在吃，今天头撞到门框了"
    )


# ---------------------------------------------------------------- [3][10]
def test_own_snapshot_restore_recovers_deleted_row(tmp_path):
    # 恢复本机快照时，快照导出后才合并进来的外来编辑孪生不能
    # 顶替被删记录（第一百一十五轮 [3][10]）
    old = _store(tmp_path, "s3o")
    other = _store(tmp_path, "s3b")
    target = _store(tmp_path, "s3t")
    try:
        for st in (old, other, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        old.add_reminder("复查", _day(7), note="", profile="妈妈")
        target.import_data(old.export_data())
        target._now = lambda: "2026-06-02T08:00:00"
        snapshot = target.export_data()
        other.add_reminder("吃药", _day(19), note="n2", profile="妈妈")
        target._now = lambda: "2026-06-03T08:00:00"
        target.import_data(other.export_data())
        rows = target.list_reminders(True, "妈妈")
        twin = next(r for r in rows if r["title"] == "吃药")
        first = next(r for r in rows if r["title"] == "复查")
        target._now = lambda: "2026-10-01T09:00:00"
        target.update_reminder(int(twin["id"]), due_at=_day(20))
        target.delete_reminder(int(first["id"]))
        added = target.import_data(snapshot).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        assert added == 1
        assert any(r["title"] == "复查" for r in rows)
        assert len(rows) == 2
    finally:
        old.close()
        other.close()
        target.close()


# ---------------------------------------------------------------- [4]
def test_merge_backup_keeps_other_source_reminders(tmp_path):
    # 合并备份：另一来源创建的同秒提醒不能被吞并，重复导入幂等
    # （第一百一十五轮 [4]）
    src_a = _store(tmp_path, "s4a")
    src_b = _store(tmp_path, "s4b")
    target = _store(tmp_path, "s4t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
            st.save_profile("爸爸", {})
        src_a.add_reminder(
            "复诊", "2026-06-07", note="", profile="妈妈",
            advance_days=1, repeat_days=7,
        )
        src_a.add_reminder("吃药", "2026-06-21", note="n2", profile="妈妈")
        backup_a = src_a.export_data()
        src_b.add_reminder("复诊", "2026-06-07", note="n2", profile="妈妈")
        src_b.add_reminder("测血压", "2026-06-21", profile="妈妈")
        src_b.add_reminder("复诊", "2026-06-07", note="n2", profile="爸爸")
        src_b.add_reminder("复诊", "2026-06-21", note="n2", profile="爸爸")
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        assert target.import_data(backup_b).get("reminders") == 4
        assert target.import_data(backup_b).get("reminders") == 0
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "媳妇胸痛是什么病",
        "丈母娘抽搐是什么病",
        "老丈人胸痛是什么病",
        "病人胸痛是什么病",
    ],
)
def test_patient_subject_markers_extended(text):
    # 媳妇/丈母娘/老丈人/病人是患者或家属主语，定义问句前的急症
    # 主诉要保留（第一百一十五轮 [5]）
    assert _level(text) == "emergency", _red(text)


def test_patient_subject_marker_control():
    assert _level("岳母抽搐是什么病") == "emergency"


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "我之前吃华法林，上周停了，现在牙疼能吃布洛芬吗",
        "我之前吃华法林，上周末停了，现在牙疼能吃布洛芬吗",
    ],
)
def test_recent_stop_last_week_cn(text):
    # 上周/上周末停药仍在抗凝风险窗口（第一百一十五轮 [6]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_recent_stop_last_week_control():
    result = check_medication_safety(
        "我之前吃华法林，一周前停了，现在牙疼能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [7]
def test_cross_profile_twin_needs_same_soft(tmp_path):
    # 属主备份行的软字段（标题）不同时不能互换认领，双胞胎各插
    # 各的（第一百一十五轮 [7]）
    src = _store(tmp_path, "s7s")
    target = _store(tmp_path, "s7t")
    try:
        for st in (src, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
            st.save_profile("爸爸", {})
        target.add_reminder("复查", _day(7), note="n1", profile="爸爸")
        src.add_reminder("吃药", _day(7), note="n2", profile="爸爸")
        src.add_reminder("复查", _day(7), note="n1", profile="妈妈")
        target.import_data(src.export_data())
        rows = target.list_reminders(True, None)
        assert any(
            r["title"] == "复查" and r["profile"] == "妈妈" for r in rows
        )
        assert any(
            r["title"] == "吃药" and r["profile"] == "爸爸" for r in rows
        )
        assert len(rows) == 3
    finally:
        src.close()
        target.close()


# ---------------------------------------------------------------- [8]
def test_repeat_import_legacy_backup_idempotent(tmp_path):
    # 同一旧备份重复导入：标记型遗留去重不能复活重复预约与待办
    # （第一百一十五轮 [8]）
    store = _store(tmp_path, "s8")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = {
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": _day(-2),
                 "diagnosis": "高血压", "next_followup_date": _day(20),
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 30 + i, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": _day(20) + "T09:00",
                 "status": "upcoming", "owner_visit_id": 0,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED}
                for i in range(3)
            ],
            "reminders": [
                {"id": 40 + i, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": _day(19), "note": "复诊",
                 "appointment_id": 30 + i, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED}
                for i in range(3)
            ],
        }
        snapshots = []
        for _ in (1, 2):
            store.import_data(forged)
            vid = int(store.list_visit_records("妈妈")[0]["id"])
            owned = [
                a for a in store.list_appointments("妈妈", None)
                if a["status"] == "upcoming"
                and (
                    int(a.get("owner_visit_id") or 0) == vid
                    or f"由就诊记录 #{vid} 自动创建"
                    in str(a.get("note") or "")
                )
            ]
            pending = store.list_reminders(False, "妈妈")
            snapshots.append((len(owned), len(pending)))
        assert snapshots == [(1, 1), (1, 1)]
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
@pytest.mark.parametrize(
    "text",
    ["华法林，停了三天", "华法林 停用一周"],
)
def test_context_names_keep_recent_stopped_drug(text):
    # 天/周级近期停药仍按在用药物检查（第一百一十五轮 [9]）
    names = api_server._context_medication_names({"medications": text})
    result = api_server._evaluate_medication_safety(
        names + ["布洛芬"], [], "62", "女", [], []
    )
    assert "华法林与非甾体抗炎药" in _titles(result), (names, result)


def test_context_names_long_stop_control():
    names = api_server._context_medication_names(
        {"medications": "华法林，停了两个月"}
    )
    assert not any("华法林" in n for n in names), names


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    [
        "今早停了华法林，能吃布洛芬吗",
        "今晨停了华法林，能吃布洛芬吗",
        "Last night I stopped taking warfarin, can I take ibuprofen?",
        "This morning I stopped taking warfarin, can I take"
        " ibuprofen?",
    ],
)
def test_recent_stop_this_morning(text):
    # 今早/今晨与英文前置语序同属近期停药（第一百一十五轮 [11]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "context",
    [
        {"history": "我爹在吃华法林", "age": "60岁"},
        {"history": "娘在吃华法林", "age": "60岁"},
        {"history": "叔叔在吃华法林", "age": "60岁"},
        {"history": "阿姨在吃华法林", "age": "60岁"},
        {"medications": "孩子在吃华法林", "age": "60岁"},
        {"history": "家里老人在吃华法林", "age": "60岁"},
    ],
)
def test_family_terms_not_patient_anticoagulant(context):
    # 成人档案下家属称谓的华法林不能算患者本人（第一百一十五轮 [12]）
    assert _level("今天头撞到门框了", context) == "none", _red(
        "今天头撞到门框了", context
    )


def test_family_terms_fever_chemo():
    assert _level(
        "今天发烧39度", {"history": "娘在做化疗", "age": "60岁"}
    ) == "none"


def test_family_terms_patient_control():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框了", WARFARIN
    )


# ---------------------------------------------------------------- [13]
def test_id_collision_does_not_duplicate_edited_row(tmp_path):
    # 本地行主键恰等于另一条未处理备份行时，不能把本行自己的编辑
    # 版本再补插一份旧副本（第一百一十五轮 [13]）
    src = _store(tmp_path, "s13s")
    target = _store(tmp_path, "s13t")
    try:
        for st in (src, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
            st.save_profile("爸爸", {})
        src.add_reminder("吃药", "2026-06-07", note="n2", profile="妈妈")
        src.add_reminder("其它", "2026-06-08", profile="爸爸")
        src.add_reminder("复查", "2026-06-09", note="n2", profile="妈妈")
        backup = src.export_data()
        target.add_reminder("复查", "2026-06-07", profile="爸爸")
        target.add_reminder("其它", "2026-06-08", profile="爸爸")
        target.import_data(backup)
        rows = target.list_reminders(True, "妈妈")
        med_row = next(r for r in rows if r["title"] == "吃药")
        target._now = lambda: "2026-10-01T09:00:00"
        target.update_reminder(int(med_row["id"]), due_at="2026-06-10")
        assert target.import_data(backup).get("reminders") == 0
        rows = target.list_reminders(True, "妈妈")
        assert len([r for r in rows if r["title"] == "吃药"]) == 1
    finally:
        src.close()
        target.close()


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "华法林，已经停了两个月",
        "华法林，两个月前就停了",
        "华法林，半年前停的",
        "华法林，去年就停了",
    ],
)
def test_stop_modifiers_drop_drug(text):
    # 已经/就/去年/停的 等写法同样是停药标注（第一百一十五轮 [14]）
    names = api_server._context_medication_names({"medications": text})
    result = api_server._evaluate_medication_safety(
        names + ["布洛芬"], [], "62", "女", [], []
    )
    assert "华法林与非甾体抗炎药" not in _titles(result), (names, result)


# ---------------------------------------------------------------- [15]
def test_family_allergy_terms_extended():
    # 媳妇对青霉素过敏不能算患者本人（第一百一十五轮 [15]）
    result = check_medication_safety(
        "我能吃头孢克肟吗",
        {"age": "70", "allergies": "媳妇对青霉素过敏"},
    )
    assert not any("青霉素" in str(t) for t in _titles(result)), result
    result = check_medication_safety(
        "我能吃头孢克肟吗",
        {"age": "70", "allergies": "我对青霉素过敏"},
    )
    assert any("青霉素" in str(t) for t in _titles(result)), result


@pytest.mark.parametrize(
    "text",
    [
        "华法林 3mg 每日一次，孙子吃布洛芬",
        "华法林 3mg 每日一次，孙女吃布洛芬",
        "华法林 3mg 每日一次，闺女吃布洛芬",
        "华法林 3mg 每日一次，老太太吃布洛芬",
        "华法林 3mg 每日一次，外甥吃布洛芬",
    ],
)
def test_family_medication_terms_extended(text):
    names = api_server._context_medication_names({"medications": text})
    result = api_server._evaluate_medication_safety(
        names, [], "70", "女", [], []
    )
    assert "华法林与非甾体抗炎药" not in _titles(result), result


# ---------------------------------------------------------------- [16]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_queued_send_hint_accurate():
    source = _app_js()
    assert "function concurrencyBlockedHint()" in source
    assert "主诉正在提交，请稍候再操作。" in source
    start = source.index("async function applySelectedProfile(")
    block = source[start:start + 4000]
    assert "window.alert(concurrencyBlockedHint());" in block
    # 真正的流式会诊窗口仍保留「请先停止当前会诊」
    assert (
        "if (busy) {\n"
        "    window.alert('会诊进行中，请先停止当前会诊。');\n"
        "    return false;"
        in source
    )


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "text",
    [
        "怀孕12周了，我见血就头晕",
        "怀孕12周，你说得一针见血",
        "孕12周，抽血时见到血我就紧张",
        "怀孕12周，刚才刷到一个见血封喉的新闻",
    ],
)
def test_jianxue_idioms_not_bleeding(text):
    # 一针见血/见血封喉/见血就晕不是孕产期出血（第一百一十五轮 [17]）
    assert _level(text) == "none", _red(text)


@pytest.mark.parametrize(
    "text",
    [
        "孕8周，上厕所见血了",
        "孕8周，内裤上见到血",
        "孕8周，刚才见血了",
    ],
)
def test_jianxue_real_bleeding_kept(text):
    assert "孕产期出血" in _labels(text), _red(text)
