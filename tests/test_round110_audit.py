# -*- coding: utf-8 -*-
"""第一百一十轮审查修复的回归测试。"""
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


# ---------------------------------------------------------------- [1][7]
@pytest.mark.parametrize(
    "text",
    [
        "华法林改成吃半片了，能吃布洛芬吗",
        "华法林改成只吃半片了，能吃布洛芬吗",
        "华法林改成两天一次了，能吃布洛芬吗",
    ],
)
def test_switch_target_dose_words_stay_active(text):
    # 换药目标是剂量/频次描述：仍在服用（第一百一十轮 [1][7]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_switch_target_dose_controls():
    result = check_medication_safety(
        "阿司匹林改成肠溶片了，能吃布洛芬吗", {}
    )
    assert "重复使用非甾体抗炎药" in _titles(result)
    result = check_medication_safety(
        "华法林两年前就改成氯吡格雷了，现在吃布洛芬", {}
    )
    assert result["level"] == "none"
    result = check_medication_safety(
        "华法林改成饭后吃了，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result)


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text,keep",
    [
        ("把华法林改成吃半片", "华法林"),
        ("把华法林改为隔天一次，现在吃布洛芬", "华法林"),
        ("把阿司匹林改为饭后吃，现在吃华法林", "阿司匹林"),
    ],
)
def test_profile_switch_dose_words_stay_active(text, keep):
    got = api_server._context_medication_names({"medications": text})
    assert any(keep in name for name in got), got


def test_profile_switch_controls():
    got = api_server._context_medication_names(
        {"medications": "两年前把华法林改为氯吡格雷，现在吃布洛芬"}
    )
    assert not any("华法林" in name for name in got), got


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text",
    [
        "华法林每晚吃、阿司匹林停了，布洛芬",
        "华法林一天三次、阿司匹林停了，布洛芬",
        "华法林隔天吃、阿司匹林停了，布洛芬",
    ],
)
def test_shared_stop_frequency_guard(text):
    got = api_server._context_medication_names({"medications": text})
    assert any("华法林" in name for name in got), got


# ---------------------------------------------------------------- [4]
def test_import_request_keeps_source_id():
    request = api_server.ImportSessionsRequest(
        version=1, source_id="abc123", sessions=[]
    )
    assert request.model_dump(exclude_unset=True).get("source_id") == "abc123"


def test_web_import_body_passes_source_id():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "source_id: data.source_id," in source


# ---------------------------------------------------------------- [2][5]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病，我女儿很担心，需要去医院吗",
        "胸口疼是什么病，我老公很担心，需要去医院吗",
        "咯血是什么病，我妈妈很害怕，要不要去医院",
    ],
)
def test_bystander_family_keeps_help_clause(text):
    # 家属只是旁观担心：求助续句仍算患者语境（第一百一十轮 [2][5]）
    assert _is_emergency_for(text), _red(text)


def _is_emergency_for(text):
    result = _red(text)
    return result["level"] == "emergency" and bool(result["matches"])


def test_family_acute_symptom_keeps_help_clause():
    assert "抽搐" in _labels(
        "抽搐是什么病，我儿子抽了五分钟了，需要去医院吗"
    )


def test_family_symptom_control():
    assert _level("胸痛是什么病，我爸咳嗽，需要去医院吗") == "none"


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病，需要马上去医院吗",
        "胸痛是什么病，要马上去医院吗",
        "胸痛是什么病，需要立刻去医院吗",
        "胸痛是什么病，需要赶紧去医院吗",
        "胸痛是什么病，要不要马上去医院",
        "胸痛是什么病，需要去医院看看吗",
    ],
)
def test_urgent_help_adverbs(text):
    assert "胸痛" in _labels(text), _red(text)


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "摔了一跤，后脑勺也在流血",
        "摔了一跤，后脑勺都在流血",
        "摔了一跤，后脑勺出了点血",
        "后脑勺流了一点血",
    ],
)
def test_head_trauma_stacked_adverbs(text):
    # 叠用副词与量化出血（第一百一十轮 [6]）
    assert "抗凝治疗期间头部外伤" in _labels(text, WARFARIN), text


def test_head_trauma_stacked_controls():
    assert "抗凝治疗期间头部外伤" in _labels(
        "后脑勺还在流血", WARFARIN
    )


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text",
    [
        "摔了一跤，头起了个大包",
        "不小心磕了一下，头肿了个大包",
        "头鼓了个包",
    ],
)
def test_head_swelling_bare_tou(text):
    # 裸「头」起包同样是头部外伤（第一百一十轮 [8]）
    assert "抗凝治疗期间头部外伤" in _labels(text, WARFARIN), text


def test_head_swelling_controls():
    assert _level("脚趾头肿了个包", WARFARIN) == "none"


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize(
    "text",
    [
        "帮我看看什么是咯血",
        "帮我查查什么是咯血",
        "帮忙查一下什么是咯血",
        "帮我了解一下什么是咯血",
    ],
)
def test_educational_help_frames(text):
    # 叠词与「帮忙」框架（第一百一十轮 [15]）
    assert _level(text) == "none", _red(text)


def test_educational_help_controls():
    assert _level("帮我查一下什么是咯血") == "none"


# ---------------------------------------------------------------- [9]
def test_inherited_backup_edit_stays_idempotent(tmp_path):
    src_a = _store(tmp_path, "f9a")
    store_b = _store(tmp_path, "f9b")
    store_c = _store(tmp_path, "f9c")
    try:
        for st in (src_a, store_b, store_c):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_medication(
            "妈妈", "药M", dose="5mg", frequency="每日一次", note="n1",
        )
        backup_a = src_a.export_data()
        store_b.import_data(backup_a)
        store_b._now = lambda: "2026-06-02T08:00:00"
        backup_b = store_b.export_data()
        store_c.import_data(backup_a)
        assert store_c.import_data(backup_b).get("medications") == 0
        med = store_c.list_medications("妈妈", None)[0]
        store_c._now = lambda: "2026-10-01T09:00:00"
        store_c.update_medication(int(med["id"]), dose="10mg")
        # 备份含继承记录：恢复本安装备份仍认领本地编辑行，不复活
        # 旧副本（第一百一十轮 [9]）
        added = store_c.import_data(backup_b).get("medications")
        rows = store_c.list_medications("妈妈", None)
        assert added == 0, [(r["id"], r["dose"]) for r in rows]
        assert len(rows) == 1 and str(rows[0]["dose"]) == "10mg", rows
    finally:
        for st in (src_a, store_b, store_c):
            st.close()


# ---------------------------------------------------------------- [10]
def test_tombstone_blocks_restored_followup_reminder(tmp_path):
    d0 = _day(20)
    store = _store(tmp_path, "f10")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        visit = store.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=d0,
        )
        vid = int(visit["id"])
        appt = store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=d0 + "T09:00",
            note=f"由就诊记录 #{vid} 自动创建", owner_visit_id=vid,
        )
        aid = int(appt["id"])
        rid = int(store.add_reminder(
            "复诊：高血压", _day(19), note="复诊 " + d0,
            profile="妈妈", appointment_id=aid,
        )["id"])
        store.set_visit_followup_links(vid, aid, rid)
        backup = store.export_data()
        store._now = lambda: "2026-06-02T08:00:00"
        store.delete_reminder(rid)
        assert store.list_reminders(False, "妈妈") == []
        store.import_data(backup)
        # 用户墓碑 opt-out：恢复备份不能复活挂预约的复诊提醒
        # （第一百一十轮 [10]）
        assert store.list_reminders(False, "妈妈") == []
    finally:
        store.close()


# ---------------------------------------------------------------- [16]
def test_import_origin_map_survives_large_table(tmp_path):
    store = _store(tmp_path, "f16")
    try:
        big = {str(i): "s%032d" % i for i in range(1, 6001)}
        store._set_import_origin_map("health_metrics", big)
        loaded = store._get_import_origin_map("health_metrics")
        # 超量时按条目裁剪并保持合法 JSON（第一百一十轮 [16]）
        assert len(loaded) > 0
    finally:
        store.close()


# ---------------------------------------------------------------- [17]
def test_untrusted_proxy_failure_key_uses_peer(monkeypatch):
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
            self.client = type("C", (), {"host": "172.18.0.2"})()

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.delenv("MEDIX_PROXY_CLIENT_IP_HEADER", raising=False)
    request = _Req({"x-forwarded-for": ["198.51.100.1"]})
    sentinel = api_server._client_ip_key(request)
    shared = api_server._shared_failure_auth_key(request, "172.18.0.2")
    # 未声明代理的失败计数按真实 TCP 对端聚合，不再共享全局哨兵桶
    # （第一百一十轮 [17]）
    assert sentinel == "ip:untrusted-proxy"
    assert shared.startswith("ip:172.18.0.2")


def _clear_auth_limiters():
    for limiter in (
        api_server.auth_failure_limiter,
        api_server.auth_failure_limiter_loopback,
        api_server.auth_failure_limiter_ipv6_global,
    ):
        with limiter._lock:
            limiter._hits.clear()


def test_undeclared_proxy_peers_do_not_share_failure_lock(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    import memory

    token = "r110-secret-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    store = SessionStore(str(tmp_path / "peer.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    _clear_auth_limiters()
    try:
        # 10 个未声明代理对端各 10 次错误令牌：旧版会把全局哨兵桶
        # ip:untrusted-proxy 填满（10×10=100）
        for index in range(10):
            attacker = TestClient(
                api_server.app, base_url="http://localhost",
                client=(f"172.18.0.{index + 10}", 50001),
            )
            for _ in range(10):
                response = attacker.get(
                    "/api/sessions",
                    headers={
                        "X-Medix-Token": "guess",
                        "X-Forwarded-For": f"10.1.2.{index}",
                    },
                )
                assert response.status_code == 401
        victim = TestClient(
            api_server.app, base_url="http://localhost",
            client=("172.18.0.99", 50001),
        )
        response = victim.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": token,
                "X-Forwarded-For": "198.51.100.200",
            },
        )
        # 其它未声明代理对端的失败不能锁死本对端的正确令牌
        # （第一百一十轮 [17]）
        assert response.status_code == 200, response.status_code
    finally:
        _clear_auth_limiters()
        store.close()


# ---------------------------------------------------------------- [13][14]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.mark.parametrize(
    "name",
    ["savePatientProfile", "renamePatientProfile", "deletePatientProfile"],
)
def test_web_profile_handlers_blocked_while_sending(name):
    source = _app_js()
    start = source.index(f"async function {name}(")
    block = source[start:start + 400]
    assert "if (busy || sendInFlight)" in block, block[:200]


def test_web_memory_fallback_keeps_profile():
    source = _app_js()
    start = source.index("async function restoreCurrentSession(")
    block = source[start:start + 30000]
    # 短期记忆回退的空档案不能清空用户已选档案（第一百一十轮 [14]）
    assert "skipProfile: pendingReset || data.source === 'memory'" in block
