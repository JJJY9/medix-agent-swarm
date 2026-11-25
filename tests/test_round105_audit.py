# -*- coding: utf-8 -*-
"""第一百零五轮审查修复的回归测试。"""
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
    "text,label",
    [
        ("什么是抽搐？已经抽了五分钟了", "抽搐"),
        ("什么叫抽搐？已经抽了五分钟了", "抽搐"),
        ("请问什么是抽搐？已经抽了五分钟了", "抽搐"),
        ("什么是胸痛？疼得我直冒冷汗", "胸痛"),
    ],
)
def test_definition_lead_keeps_following_course(text, label):
    assert label in _labels(text), text


def test_definition_lead_controls():
    assert _level("什么是抽搐") == "none"
    assert _level("介绍一下呼吸困难") == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "brand", ["芬必得", "美林", "扶他林", "西乐葆", "拜阿司匹灵"]
)
def test_nsaid_brand_names_trigger_interaction(brand):
    result = check_medication_safety(f"华法林每天吃，牙痛能吃{brand}吗", {})
    assert "华法林与非甾体抗炎药" in _titles(result), (brand, result)


def test_nsaid_brand_controls():
    # 美林同时是地名：地名用法不归一化为布洛芬
    result = check_medication_safety("华法林每天吃，我住在美林小区", {})
    assert "华法林与非甾体抗炎药" not in _titles(result)
    assert "华法林与非甾体抗炎药" in _titles(
        check_medication_safety("华法林每天吃，牙痛能吃布洛芬吗", {})
    )


# ---------------------------------------------------------------- [3]
def test_multi_candidate_skips_stale_unedited_rows(tmp_path):
    store = _store(tmp_path, "x3")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup_a = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复查", "due_at": _day(6),
             "note": "n4", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复查", "due_at": _day(20),
             "note": "n3", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        backup_b = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "吃药", "due_at": _day(40),
             "note": "n2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复查", "due_at": _day(20),
             "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup_a)
        store.import_data(backup_b)
        notes = [r["note"] for r in store.list_reminders(None)]
        # B 的妈妈行不能被内容不符（due 06 天 vs 20 天）且未编辑的
        # 旧行 min() 认领：备份内容必须入库（第一百零五轮 [3]）
        assert "n1" in notes, notes
        assert store.import_data(backup_b).get("reminders") == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "今天剖宫产，阴道出血",
        "昨天剖宫产，阴道出血",
        "前天剖宫产，阴道出血",
        "这周剖宫产，阴道出血",
        "本周剖宫产，阴道出血",
        "半个月前剖宫产，阴道出血",
        "半个多月前剖宫产，阴道出血",
        "一个多月前剖宫产，阴道出血",
        "两个多月前剖宫产，阴道出血",
        "一个半月前剖宫产，阴道出血",
        "两周半前剖宫产，阴道出血",
        "十来天前剖宫产，阴道出血",
        "上个月剖宫产，阴道出血",
    ],
)
def test_recent_caesarean_generalized(text):
    assert "孕产期出血" in _labels(text), text


def test_recent_caesarean_history_and_controls():
    assert "孕产期出血" in _labels(
        "阴道出血", {"history": "半个月前剖宫产"}
    )
    assert _level("三年前剖宫产，阴道出血") == "none"
    assert _level("一年前剖宫产，阴道出血") == "none"
    assert _level("十二个月前剖宫产，阴道出血") == "none"


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "tail",
    [
        "最近一直在吃", "最近一直在服用", "近期在吃", "这几天在吃",
        "这些天在吃", "前段时间在吃", "前阵子在吃",
    ],
)
def test_continuous_restart_variants(tail):
    result = _red(f"华法林停了两年，{tail}，今天头撞到门框")
    assert "抗凝治疗期间头部外伤" in result["matches"], tail
    med = check_medication_safety(
        f"华法林停了两年，{tail}，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(med), (tail, med)


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize("who", ["岳父", "岳母", "公公", "婆婆", "闺女"])
def test_in_law_family_not_patient(who):
    ctx = {"age": "40岁"}
    assert _level(f"我的{who}在吃华法林，我今天头撞到门框", ctx) == "none"
    assert _level(f"我{who}在吃华法林，我今天头撞到门框", ctx) == "none"


def test_in_law_family_history_and_control():
    ctx = {"age": "40岁"}
    assert _level(
        "我今天头撞到门框", {**ctx, "history": "我的岳父长期吃华法林"}
    ) == "none"
    assert "抗凝治疗期间头部外伤" in _labels(
        "我在吃华法林，今天头撞到门框", ctx
    )


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text,label",
    [
        ("孙子抽搐见于什么病", "抽搐"),
        ("外甥胸痛见于什么病", "胸痛"),
        ("老爷子胸痛的原因有哪些", "胸痛"),
        ("阿姨胸痛的原因有哪些", "胸痛"),
        ("闺女抽搐见于什么病", "抽搐"),
    ],
)
def test_definition_question_family_markers(text, label):
    assert label in _labels(text), text


def test_definition_question_family_controls():
    assert "胸痛" in _labels("奶奶胸痛见于什么病")
    assert _level("胸痛见于什么病") == "none"


# ---------------------------------------------------------------- [8]
def test_bare_stop_object_duration_triage():
    assert _level("我停华法林已经两年了，今天头撞到门框") == "none"
    assert _level("停华法林两年了，今天头撞到门框") == "none"


def test_bare_stop_object_duration_medication():
    assert check_medication_safety(
        "我停华法林两年了，能吃布洛芬吗", {}
    )["level"] == "none"
    assert check_medication_safety(
        "我停华法林已经两年了，能吃布洛芬吗", {}
    )["level"] == "none"


def test_bare_stop_object_duration_controls():
    # 停经不是停药；仍在服药仍是阳性
    assert _level("停经两个月了，今天头撞到门框") == "none"
    assert "抗凝治疗期间头部外伤" in _labels(
        "我在吃华法林，今天头撞到门框"
    )


# ---------------------------------------------------------------- [9]
def test_drift_candidate_keeps_done_state(tmp_path):
    store = _store(tmp_path, "x9")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        backup_a = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "复诊", "due_at": _day(6),
             "note": "已完成复查", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        backup_b = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复查", "due_at": _day(40),
             "note": "n3", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "复诊", "due_at": _day(6),
             "note": "已完成复查", "done": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "爸爸", "title": "吃药", "due_at": _day(20),
             "note": "n3", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup_a)
        store.import_data(backup_b)
        dads = [
            r for r in store.list_reminders(True, None)
            if r["profile"] == "爸爸"
            and str(r["due_at"])[:10] == _day(6)
        ]
        # 未编辑且 done/note 不符的候选不是本行版本：备份的完成状态
        # 必须按记录补回（第一百零五轮 [9]）
        assert any(bool(r["done"]) for r in dads), dads
        assert store.import_data(backup_b).get("reminders") == 0
        assert store.import_data(backup_b).get("reminders") == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_delete_unrelated_old_appointment_keeps_reminder(tmp_path):
    store = _store(tmp_path, "x10")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        d0 = _day(8)
        vid = int(store.add_visit_record(
            "妈妈", visit_date=_day(-3), diagnosis="高血压",
            next_followup_date=d0,
        )["id"])
        a30 = int(store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d0 + "T09:00",
            owner_visit_id=vid,
        )["id"])
        r20 = int(store.add_reminder(
            "复诊：X", _day(7), note="复诊 " + d0, profile="妈妈",
        )["id"])
        store.set_visit_followup_links(vid, a30, r20)
        a31 = int(store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d0 + "T09:00",
            owner_visit_id=vid, status="cancelled",
        )["id"])
        store._now = lambda: LATER
        store.delete_appointment(a31)
        rem = store.get_reminder(r20)
        visit = store.get_visit_record(vid)
        # 就诊已链接另一条待就诊预约：删无关旧预约不能删它的提醒
        assert rem is not None
        assert int(visit["followup_reminder_id"] or 0) == r20
        # 取消变体（仅备注标记的遗留预约）
        a40 = int(store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d0 + "T09:00",
            note=f"由就诊记录 #{vid} 自动创建",
        )["id"])
        store.update_appointment_partial(a40, status="cancelled")
        assert store.get_reminder(r20) is not None
        assert int(
            store.get_visit_record(vid)["followup_reminder_id"] or 0
        ) == r20
        # 对照：取消真正的复诊预约仍清空链接
        store.update_appointment_partial(a30, status="cancelled")
        assert int(
            store.get_visit_record(vid)["followup_reminder_id"] or 0
        ) == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    [
        "停用阿司匹林后继续服用氯吡格雷",
        "停用阿司匹林后一直服用氯吡格雷",
        "停用阿司匹林后长期服用氯吡格雷",
        "停药后继续吃氯吡格雷",
    ],
)
def test_switch_verb_with_adverbs(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["氯吡格雷"], text


# ---------------------------------------------------------------- [12]
def test_medication_allergy_in_evaluation():
    safety = api_server._evaluate_medication_safety(
        ["阿莫西林", "对青霉素过敏"], [], "", "", [], []
    )
    assert any("青霉素" in str(t) for t in _titles(safety)), safety


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "text",
    [
        "华法林，停止服用了；阿司匹林",
        "华法林，不再服用了；阿司匹林",
        "华法林，不再服用；阿司匹林",
        "华法林，不吃了。；阿司匹林",
    ],
)
def test_stop_annotation_le_variants(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["阿司匹林"], text


def test_stop_annotation_le_chat():
    assert check_medication_safety(
        "阿司匹林（不再服用了），能吃布洛芬吗", {}
    )["level"] == "none"
    assert check_medication_safety(
        "阿司匹林（停止服用了），能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "已停用阿司匹林、氯吡格雷",
        "已停药阿司匹林、氯吡格雷、阿托伐他汀",
        "停用阿司匹林、氯吡格雷",
    ],
)
def test_shared_stop_verb_enumeration(text):
    assert api_server._context_medication_names({"medications": text}) == []


def test_shared_stop_verb_controls():
    names = api_server._context_medication_names
    assert names({"medications": "已停用阿司匹林改用氯吡格雷"}) == [
        "氯吡格雷"
    ]
    assert names(
        {"medications": "停用阿司匹林后吃氯吡格雷、奥美拉唑"}
    ) == ["氯吡格雷", "奥美拉唑"]


# ---------------------------------------------------------------- [15]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def _extract_function(source, name):
    start = source.index(f"function {name}(")
    end = source.index("\n}", start)
    return source[start:end + 2]


def test_web_prefix_cleanup_only_for_restored_records():
    if not NODE:
        pytest.skip("node 不可用，跳过 JS 纯逻辑测试")
    source = _app_js()
    function = _extract_function(source, "clearOtherSessionInflight")
    script = """
const assert = require('assert');
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

function makeApi({records = null, restored = null} = {}) {
  store = {};
  if (records) store[OTHER] = JSON.stringify(records);
  const body = [
    'var inflightRestoredText = ' + JSON.stringify((restored && restored.text) || '') + ';',
    'var inflightRestoredOwnerSession = ' + JSON.stringify((restored && restored.owner) || '') + ';',
    'var inflightRestoredKeyName = ' + JSON.stringify((restored && restored.keyName) || '') + ';',
  ].join('\\n') + '\\n' + __FUNC__ + '\\nreturn {' +
    ' clear: clearOtherSessionInflight,' +
    ' records: () => readOtherInflightRecords()' +
    '};';
  return new Function(
    'readOtherInflightRecords', 'writeOtherInflightRecords',
    'INFLIGHT_QUESTION_OTHER_KEY', body
  )(readOtherInflightRecords, writeOtherInflightRecords, OTHER);
}

const HIDDEN = '发烧三天了，需要去医院吗';

// 记录以发送文本为前缀且从未恢复展示：保留唯一副本
let api = makeApi({records: [{sessionId: 'H', question: HIDDEN, at: 0}]});
api.clear('H', '发烧');
assert.deepStrictEqual(api.records().map((r) => r.question), [HIDDEN]);

// 同一记录确实恢复展示过：按前缀清理
api = makeApi({
  records: [{sessionId: 'H', question: HIDDEN, at: 0}],
  restored: {text: HIDDEN, owner: 'H', keyName: OTHER},
});
api.clear('H', '发烧');
assert.strictEqual(api.records().length, 0);

// 发送文本包含整条记录：清理（内容已进入新落库消息）
api = makeApi({records: [{sessionId: 'H', question: HIDDEN, at: 0}]});
api.clear('H', HIDDEN + '，现在39度');
assert.strictEqual(api.records().length, 0);

// 完全相等：清理
api = makeApi({records: [{sessionId: 'H', question: HIDDEN, at: 0}]});
api.clear('H', HIDDEN);
assert.strictEqual(api.records().length, 0);

// 其它会话记录不受影响
api = makeApi({records: [{sessionId: 'W', question: HIDDEN, at: 0}]});
api.clear('H', '发烧');
assert.strictEqual(api.records().length, 1);

console.log('ok');
"""
    script = script.replace("__FUNC__", json.dumps(function))
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
