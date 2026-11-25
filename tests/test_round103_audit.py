# -*- coding: utf-8 -*-
"""第一百零三轮审查修复的回归测试。"""
import json
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
CREATED = "2026-06-01T08:00:00"
LATER = "2026-11-01T09:00:00"


def _store(tmp_path, tag="sessions"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _day(offset):
    return (date.today() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _labels(text, context=None):
    return _red(text, context)["matches"]


def _level(text, context=None):
    return _red(text, context)["level"]


# ---------------------------------------------------------------- [1][2]
@pytest.mark.parametrize(
    "text",
    [
        "胸部正中疼痛",
        "胸部正中疼痛，冒冷汗",
        "胸骨正中疼痛",
        "胸骨中段疼痛",
        "胸部中下段疼痛",
        "胸部中线疼痛",
        "胸部中央疼痛",
        "胸部正中间疼痛",
        "心口处疼",
        "胸部位置疼痛",
        "胸部上方疼痛",
        "胸口压着疼",
    ],
)
def test_chest_pain_center_position_variants(text):
    assert "胸痛" in _labels(text), text


@pytest.mark.parametrize(
    "text",
    [
        "心前区压榨性疼痛",
        "心前区压榨样疼痛",
        "胸部正中压榨样疼痛",
        "胸前区压榨性疼痛",
        "心口正中绞痛",
        "胸口压迫感",
        "心前区压迫感",
        "胸口紧缩感",
        "胸骨后烧灼样疼痛",
    ],
)
def test_chest_pain_suffix_and_pressure_variants(text):
    assert "胸痛" in _labels(text), text


def test_chest_pain_center_controls():
    assert _level("没有胸痛") == "none"
    assert _level("胸口压迫感没有了") == "none"
    assert _level("没有压迫感") == "none"
    assert _level("胸部外伤后处理") == "none"


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "obj",
    ["这个药", "那种药", "这种药", "这药", "该药", "此药", "它", "药"],
)
def test_restart_pronoun_object_keeps_interaction(obj):
    text = f"华法林停了半年，现在在吃{obj}，能吃布洛芬吗"
    result = check_medication_safety(text, {})
    assert result["level"] == "warning", obj
    assert any(
        alert.get("title") == "华法林与非甾体抗炎药"
        for alert in result["alerts"]
    ), obj


def test_restart_pronoun_object_controls():
    # 真正换了别的药/非药物对象仍不算本药续用
    result = check_medication_safety(
        "华法林停了半年，现在在吃感冒药，能吃布洛芬吗", {}
    )
    assert result["level"] == "none"
    # 换成阿司匹林：华法林不得被复活（阿司匹林自身与布洛芬的
    # 重复用药提示不受影响）
    result = check_medication_safety(
        "华法林停了半年，现在在吃阿司匹林，能吃布洛芬吗", {}
    )
    assert not any(
        alert.get("title") == "华法林与非甾体抗炎药"
        for alert in result["alerts"]
    )


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "胚胎移植后第10天，阴道出血",
        "试管移植第十天，见红",
        "囊胚移植后出血",
        "冻胚移植后第7天，阴道有点出血",
        "人工授精后12天，阴道出血",
    ],
)
def test_ivf_transfer_bleeding_is_pregnancy_bleeding(text):
    assert "孕产期出血" in _labels(text), text


def test_ivf_transfer_bleeding_controls():
    assert "孕产期出血" in _labels("移植后14天验孕阳性，阴道出血")
    assert _level("没有怀孕，阴道出血") == "none"


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "history",
    ["华法林停服半年了", "华法林停止服用半年了"],
)
def test_stop_service_medication_inactive(history):
    assert _level("今天头撞到门框", {"history": history}) == "none"


def test_stop_service_immunosuppression_inactive():
    assert (
        _level("今天发烧38.9度", {"history": "甲氨蝶呤停服半年了"})
        == "none"
    )


def test_stop_service_restart_still_active():
    result = _red(
        "华法林停服半年了，现在又在吃，今天头撞到门框"
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛没有出现",
        "胸痛未出现",
        "胸痛没有发生",
        "最近胸痛没有发作",
        "胸痛没再出现",
        "胸痛未再出现",
        "呼吸困难没有出现",
        "咯血未出现",
    ],
)
def test_trailing_negation_with_fillers(text):
    assert _level(text) == "none", text


def test_trailing_negation_filler_controls():
    # 未缓解/未见好转不是否认
    assert "胸痛" in _labels("胸痛未缓解")
    assert "胸痛" in _labels("胸痛未见好转")
    # 枚举整组否定仍只覆盖带群标记的整组
    assert _level("胸痛、呼吸困难都没有出现") == "none"
    assert "胸痛" in _labels("胸痛很厉害、呼吸困难没有出现")


# ---------------------------------------------------------------- [7]
def test_twin_edited_note_reimport_idempotent(tmp_path):
    store = _store(tmp_path, "x7")
    store._now = lambda: "2025-01-01T08:00:00"
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        # 库内先有低 id 无关行：主键空间与备份不同源
        store.add_reminder("爸爸旧提醒", _day(40), note="u", profile="爸爸")
        store._now = lambda: CREATED
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复诊",
             "due_at": _day(6), "note": "nA", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊",
             "due_at": _day(6), "note": "nB", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = {
            r["note"]: int(r["id"])
            for r in store.list_reminders(False, "妈妈")
        }
        assert set(rows) == {"nA", "nB"}, rows
        store._now = lambda: LATER
        store.update_reminder(rows["nB"], note="nX")
        added = store.import_data(backup).get("reminders")
        final = sorted(
            r["note"] for r in store.list_reminders(False, "妈妈")
        )
        # 兄弟行另有未编辑的存活行：候选是 nB 被编辑后的版本，
        # 不能补插改前旧副本（第一百零三轮 [7]）
        assert added == 0, (added, final)
        assert final == ["nA", "nX"], final
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def test_visit_date_align_keeps_pending_reminder(tmp_path):
    store = _store(tmp_path, "x8")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        d0, d1 = _day(5), _day(10)
        backup = {
            "appointments": [{
                "id": 30, "profile": "妈妈", "title": "复诊：G",
                "note": "由就诊记录 #10 自动创建", "owner_visit_id": 10,
                "status": "upcoming", "scheduled_at": d1 + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 20, "profile": "妈妈", "title": "复诊：G",
                "due_at": _day(9), "appointment_id": 0, "done": 0,
                "note": "复诊 " + d0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 10, "profile": "妈妈", "visit_date": _day(-3),
                "diagnosis": "高血压", "next_followup_date": d0,
                "followup_appointment_id": 0, "followup_reminder_id": 20,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        vid = int(store.list_visit_records("妈妈")[0]["id"])
        store._now = lambda: LATER
        store.update_visit_record_partial(vid, next_followup_date=d1)
        visit = store.get_visit_record(vid)
        appointment_id = int(visit["followup_appointment_id"] or 0)
        assert appointment_id > 0, visit
        assert visit.get("followup_scheduled") is True, visit
        pending = store.list_reminders(False, "妈妈")
        # 改到与遗留自动预约同日：唯一待办提醒必须接管到该预约，
        # 不能「已安排复诊」却没有提醒（第一百零三轮 [8]）
        assert len(pending) == 1, pending
        assert int(pending[0]["appointment_id"] or 0) == appointment_id
        assert int(visit["followup_reminder_id"] or 0) == int(
            pending[0]["id"]
        )
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_visit_profile_migration_keeps_done_reminder_time(tmp_path):
    store = _store(tmp_path, "x9")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        appt = store.add_appointment(
            "妈妈", "复诊：心内", scheduled_at=_day(6) + "T09:00"
        )
        reminder = store.add_reminder(
            "复诊：心内", _day(5), note="复诊 " + _day(6),
            profile="妈妈", appointment_id=int(appt["id"]),
        )
        visit = store.add_visit_record(
            "妈妈", visit_date=_day(-2), diagnosis="高血压",
            next_followup_date=_day(6),
        )
        store.set_visit_followup_links(
            int(visit["id"]), int(appt["id"]), int(reminder["id"])
        )
        store._now = lambda: "2026-11-01T10:00:00"
        store.complete_reminder(int(reminder["id"]))
        extra = store.add_reminder(
            "就诊：提醒", _day(6), note="p", profile="妈妈",
            appointment_id=int(appt["id"]),
        )
        store._now = lambda: "2026-11-01T11:00:00"
        store.delete_reminder(int(extra["id"]))
        store._now = lambda: "2026-11-01T12:00:00"
        store.update_visit_record_partial(
            int(visit["id"]), profile="爸爸"
        )
        done_after = store.get_reminder(int(reminder["id"]))
        # 已完成提醒只迁档案，updated_at 不变，不能晚于删除墓碑
        assert done_after["updated_at"] == "2026-11-01T10:00:00"
        assert done_after["profile"] == "爸爸"
        store._now = lambda: "2026-11-01T13:00:00"
        store.update_appointment_partial(
            int(appt["id"]), scheduled_at=_day(12) + "T09:00"
        )
        pending = store.list_reminders(False, "爸爸")
        # 改期不能复活用户已删除的提醒（第一百零三轮 [9]）
        assert pending == [], pending
    finally:
        store.close()


# ---------------------------------------------------------------- [10][12]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林，已停用两年半",
        "华法林、阿司匹林，已停用一年半",
        "华法林、阿司匹林，已停用半年多",
        "华法林、阿司匹林，已停用两年多",
        "华法林、阿司匹林，已停用一年多",
        "华法林、阿司匹林，已停用两周半",
        "华法林、阿司匹林，已停用两年左右",
        "华法林、阿司匹林，已停用三个月左右",
        "华法林、阿司匹林，已停用两个月零五天",
        "华法林、阿司匹林，已停用两个月（2024年9月）",
        "华法林、阿司匹林（停用半年多）",
        "华法林、阿司匹林停用半年多",
    ],
)
def test_compound_stop_duration_removes_drug(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["华法林"], text


def test_compound_stop_duration_controls():
    names = api_server._context_medication_names
    # 尾随时长修饰药名时不能 pop 前一条在用药物（第一百零二轮 [11]）
    assert names({"medications": "华法林、停了两个月的阿司匹林"}) == [
        "华法林"
    ]
    assert names({"medications": "华法林、阿司匹林，已停用两年"}) == [
        "华法林"
    ]
    assert names({"medications": "华法林、已停药（2025年10月）"}) == []


# ---------------------------------------------------------------- [11]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def _extract_function(source, name):
    start = source.index(f"function {name}(")
    end = source.index("\n}", start)
    return source[start:end + 2]


def test_web_clear_inflight_requires_restored_or_text_match():
    source = _app_js()
    start = source.index("function clearOtherSessionInflight")
    end = source.index("function removeInflightRecord", start)
    block = source[start:end]
    # 同会话记录只在恢复过或文本命中时删除（第一百零三轮 [11]）
    assert "const restoredHere = Boolean(" in block
    assert "return !(textMatch || restoredHere);" in block
    # 其它会话唯一副本保护与文本包含语义保持不变（第八十一/八十二轮）
    assert "sameSession" in block
    assert "itemOwner !== String(sessionId)" in block
    assert "wantedQuestion.startsWith(recordQuestion)" in block
    assert "preserveOtherSessionInflight(" in source


def test_web_inflight_cleanup_behavior():
    if not NODE:
        pytest.skip("node 不可用，跳过 JS 纯逻辑测试")
    source = _app_js()
    functions = "\n".join(
        _extract_function(source, name)
        for name in (
            "clearOtherSessionInflight",
            "preserveOtherSessionInflight",
        )
    )
    script = """
const assert = require('assert');
const MAIN = 'medix.inflightQuestion';
const OTHER = 'medix.inflightQuestionOther';

function readOtherInflightRecords() {
  try {
    const raw = store[OTHER];
    const data = raw ? JSON.parse(raw) : [];
    return Array.isArray(data) ? data : [];
  } catch { return []; }
}
function writeOtherInflightRecords(records) {
  if (records.length) store[OTHER] = JSON.stringify(records.slice(0, 8));
  else delete store[OTHER];
}
let store = {};

function makeApi({records = null, main = null, restored = null} = {}) {
  store = {};
  if (records) store[OTHER] = JSON.stringify(records);
  if (main) store[MAIN] = JSON.stringify(main);
  const els = { input: { value: '' } };
  const body = [
    'var inflightRestoredText = ' + JSON.stringify((restored && restored.text) || '') + ';',
    'var inflightRestoredOwnerSession = ' + JSON.stringify((restored && restored.owner) || '') + ';',
    'var inflightRestoredKeyName = ' + JSON.stringify((restored && restored.keyName) || '') + ';',
    'var els = elsRef;',
  ].join('\\n') + '\\n' + __FUNCS__ + '\\nreturn {' +
    ' clear: clearOtherSessionInflight,' +
    ' preserve: preserveOtherSessionInflight,' +
    ' records: () => readOtherInflightRecords(),' +
    ' main: () => (store[MAIN] ? JSON.parse(store[MAIN]) : null)' +
    '};';
  return new Function(
    'safeSessionGet', 'safeSessionSet', 'safeSessionRemove',
    'readOtherInflightRecords', 'writeOtherInflightRecords',
    'INFLIGHT_QUESTION_KEY', 'INFLIGHT_QUESTION_OTHER_KEY',
    'elsRef', body
  )(
    (key) => (key in store ? store[key] : null),
    (key, value) => { store[key] = value; },
    (key) => { delete store[key]; },
    readOtherInflightRecords,
    writeOtherInflightRecords,
    MAIN, OTHER, els
  );
}

// 未恢复展示过的同会话记录：本次发送别的文本不能删除
let api = makeApi({records: [{sessionId: 'H', question: 'Q', at: 0}]});
api.clear('H', 'D 头晕两天');
assert.deepStrictEqual(api.records().map((r) => r.question), ['Q']);

// 恢复过（本页放回并发送）的同会话记录：发送后清理
api = makeApi({
  records: [{sessionId: 'H', question: 'Q', at: 0}],
  restored: {text: 'Q', owner: 'H', keyName: OTHER},
});
api.clear('H', 'Q');
assert.strictEqual(api.records().length, 0);

// 文本包含语义保持：发送的是记录文本的追加版本
api = makeApi({records: [{sessionId: 'H', question: 'Q', at: 0}]});
api.clear('H', 'Q 加重了');
assert.strictEqual(api.records().length, 0);

// 其它会话的唯一副本：本会话发送不能清理
api = makeApi({records: [{sessionId: 'W', question: 'Q', at: 0}]});
api.clear('H', 'Q');
assert.deepStrictEqual(api.records().map((r) => r.sessionId), ['W']);

// 主槽里同会话但未恢复展示的旧主诉：发送新问题前转入备份槽
api = makeApi({main: {sessionId: 'H', question: 'Q', at: 0}});
api.preserve('H', 'D 头晕两天');
assert.deepStrictEqual(api.records().map((r) => r.question), ['Q']);
assert.strictEqual(api.main().question, 'Q');

// 重发同一主诉：不需要额外备份
api = makeApi({main: {sessionId: 'H', question: 'Q', at: 0}});
api.preserve('H', 'Q');
assert.strictEqual(api.records().length, 0);

// 其它会话的现有行为不变：始终转入备份槽
api = makeApi({main: {sessionId: 'W', question: 'Q', at: 0}});
api.preserve('H', 'D');
assert.deepStrictEqual(api.records().map((r) => r.sessionId), ['W']);

// 解析失败/无记录：静默跳过
api = makeApi({});
api.preserve('H', 'D');
assert.strictEqual(api.records().length, 0);

console.log('ok');
"""
    script = script.replace("__FUNCS__", json.dumps(functions))
    result = subprocess.run(
        [NODE, "-e", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# ---------------------------------------------------------------- [13]
def test_twin_restore_claim_owner_reimport_idempotent(tmp_path):
    store = _store(tmp_path, "x13")
    store._now = lambda: "2025-01-01T08:00:00"
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.add_reminder("爸爸旧提醒", _day(40), note="u", profile="爸爸")
        store._now = lambda: CREATED
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复诊",
             "due_at": _day(6), "note": "a", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "复诊",
             "due_at": _day(6), "note": "b", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "妈妈", "title": "复诊",
             "due_at": _day(6), "note": "c", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        ids = {
            (r["profile"], r["note"]): int(r["id"])
            for r in store.list_reminders(False, None)
        }
        store._now = lambda: LATER
        store.delete_reminder(ids[("爸爸", "b")])
        store.delete_reminder(ids[("妈妈", "c")])
        store.update_reminder(ids[("妈妈", "a")], profile="爸爸")
        first = store.import_data(backup).get("reminders")
        assert first == 2, first
        stable = store.import_data(backup).get("reminders")
        rows = sorted(
            (r["profile"], r["note"])
            for r in store.list_reminders(False, None)
        )
        # 兄弟行本轮自己认领的恢复副本仍是它的存活证据：第二次
        # 导入不能补插旧档案副本（第一百零三轮 [13]）
        assert stable == 0, (stable, rows)
        assert rows == [
            ("妈妈", "c"), ("爸爸", "a"), ("爸爸", "b"), ("爸爸", "u"),
        ], rows
        third = store.import_data(backup).get("reminders")
        assert third == 0, third
    finally:
        store.close()
