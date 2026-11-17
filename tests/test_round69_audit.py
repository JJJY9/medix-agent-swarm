"""第六十九轮审查：枚举肯定项/突然起病、他药停药辅助词与类别词、
血红色分泌物、产科出血时机词、否定铺垫语境、漂移库多候选认领、
兄弟改期歧义、同源空区间、空时间组大小、过期复诊恢复/改期、提醒
删除墓碑、正确令牌预锁定豁免、接管主诉档案归属、在途主诉合并、
记忆作用域盐原子锚定。"""
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CREATED = "2026-06-01T08:00:00"


@pytest.fixture
def store(tmp_path):
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        yield session_store
    finally:
        session_store.close()


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _red_flags(text, context=None):
    from core.triage import detect_red_flags, patient_complaint_text

    return detect_red_flags(patient_complaint_text(text), context or {})


def _freeze_instance(store, now=CREATED):
    """冻结单个 store 的时钟（不冻结目标库，编辑时间需晚于创建）。"""
    store._now = lambda: now


# ---------------------------------------------------------------- 分诊
def test_enum_negation_stops_at_explicit_positive_item():
    for text, label in (
        ("无发热、有咯血", "咯血"),
        ("无发热、有呼吸困难", "呼吸困难"),
        ("无发热、有抽搐", "抽搐"),
        ("无发热、有意识模糊", "意识改变"),
        ("无发热、有咳嗽、胸痛，需要去医院吗", "胸痛"),
        ("无发热、无咳嗽、突然胸痛怎么办", "胸痛"),
        ("无发热、但咯血", "咯血"),
    ):
        result = _red_flags(text)
        assert label in result["matches"], text
    # 全部裸症状词仍按整体否定处理
    assert _red_flags("无发热、咳嗽、胸痛")["level"] == "none"


def test_long_stop_auxiliary_and_category_attribution():
    for text in (
        "华法林没吃完，阿司匹林已经停药一年了，今天头撞到门框",
        "华法林没吃完，阿司匹林早就停了半年，今天头撞到门框",
        "华法林没吃完，阿司匹林这个药停了半年，今天头撞到门框",
    ):
        result = _red_flags(text)
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    # 类别词与当前药同类才算自指
    assert "抗凝治疗期间头部外伤" in _red_flags(
        "华法林没吃完，激素停了半年了，今天头撞到门框"
    )["matches"]
    assert "免疫功能低下伴发热" in _red_flags(
        "甲氨蝶呤没吃完，激素停了半年了，今天发烧38.9度"
    )["matches"]
    # 纯辅助词/代词宾语仍指当前药
    for text in (
        "华法林没吃完，已经停药半年了，今天头撞到门框",
        "华法林没吃完，医生让我停药半年了，今天头撞到门框",
        "华法林没吃完，这个药停了半年了，今天头撞到门框",
        "甲氨蝶呤没吃完，化疗结束五年了，今天发烧38.9度",
    ):
        assert _red_flags(text)["level"] == "none", text


def test_long_stop_auxiliary_category_medication_safety():
    from core.medication_safety import check_medication_safety

    warning = check_medication_safety(
        "华法林没吃完，阿司匹林这个药停了半年，能吃布洛芬吗", {}
    )
    assert warning["level"] == "warning"
    warning = check_medication_safety(
        "甲氨蝶呤没吃完，激素停了半年了，能吃布洛芬吗", {}
    )
    assert warning["level"] == "warning"
    for text in (
        "阿司匹林没有吃完，已经停药一年了，能吃布洛芬吗",
        "阿司匹林没吃完，最近睡眠一般，胃口也还可以，大小便正常，"
        "已经停药一年了，能吃布洛芬吗",
        # 类别词与当前药同类时等价于本药的远期停用
        "阿司匹林没吃完，抗凝药停了半年了，能吃布洛芬吗",
        "甲氨蝶呤没吃完，免疫抑制剂停了半年了，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "none", text


def test_blood_red_description_still_bleeding():
    for text in (
        "怀孕7周，下面有血红色分泌物",
        "怀孕7周，阴道有血红色分泌物",
        "孕8周，内裤上有点血红色分泌物",
        "怀孕8周，擦拭时有一点血红色",
    ):
        result = _red_flags(text)
        assert "孕产期出血" in result["matches"], text
    # 化验词形仍排除
    for text in (
        "怀孕8周，有点血红蛋白低",
        "孕妇有点血尿",
    ):
        assert _red_flags(text)["level"] == "none", text


def test_obstetric_site_timing_words_are_not_sites():
    for text in (
        "产后小便时会阴伤口出血",
        "产后大便时伤口出血",
        "产妇小便时伤口出血",
        "产后小便后擦到血",
        "产妇剖宫产术后流血",
    ):
        result = _red_flags(text)
        assert "孕产期出血" in result["matches"], text
    for text in (
        "产后20天，大便时出血",
        "产妇小便带血",
    ):
        assert _red_flags(text)["level"] == "none", text


def test_negated_context_with_subject_does_not_establish_patient():
    for text in (
        "我没有胸痛，抽搐是什么病",
        "今天没有胸痛，抽搐是什么病",
        "最近没有胸痛，抽搐是什么病",
        "没有胸痛，抽搐是什么病",
    ):
        assert _red_flags(text)["level"] == "none", text
    assert "抽搐" in _red_flags("发烧两天，抽搐是什么病")["matches"]


# ---------------------------------------------------------------- 导入
def test_metric_temporal_edit_sibling_not_claimed(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.add_metric(
            "妈妈", "weight", 60, measured_at="2026-11-01T08:00"
        )
        source.add_metric(
            "妈妈", "weight", 60, measured_at="2026-11-01T09:00"
        )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        assert target.import_data(backup)["metrics"] == 2
        by_time = {
            item["measured_at"]: item["id"]
            for item in target.list_metrics("妈妈")
        }
        assert target.delete_metric(by_time["2026-11-01T08:00"]) is True
        target.update_metric(
            by_time["2026-11-01T09:00"],
            measured_at="2026-11-01T07:30",
        )
        # 被删的 08:00 读数必须补回；09:00 不能复制出旧值
        assert target.import_data(backup)["metrics"] == 1
        rows = sorted(
            (item["measured_at"], item["value1"])
            for item in target.list_metrics("妈妈")
        )
        assert rows == [
            ("2026-11-01T07:30", 60.0),
            ("2026-11-01T08:00", 60.0),
        ]
        assert target.import_data(backup)["metrics"] == 0
    finally:
        target.close()


def test_medication_drift_multi_candidate_restores_deleted(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.save_profile("爸爸", {})
        source.add_medication("妈妈", "药X", "5mg")
        source.add_medication("爸爸", "药Y", "5mg", note="n")
        source.add_medication("妈妈", "药X", "10mg", active=False)
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        for index in range(5):
            target.add_medication("妈妈", f"占位{index}", "1片")
        assert target.import_data(backup)["medications"] == 3
        rows = []
        for profile in ("妈妈", "爸爸"):
            for item in target.list_medications(profile, limit=100):
                rows.append((profile, item))
        # 本地把两条 妈妈/X 行改挂到 爸爸，并删除 爸爸/Y 行
        for profile, item in rows:
            if profile == "妈妈" and item["name"] == "药X":
                target.update_medication(item["id"], profile="爸爸")
        for profile, item in rows:
            if profile == "爸爸" and item["name"] == "药Y":
                assert target.delete_medication(item["id"]) is True
        target.import_data(backup)
        final = []
        for profile in ("妈妈", "爸爸"):
            for item in target.list_medications(profile, limit=100):
                final.append((profile, item["name"], item["dose"]))
        # 被删的 爸爸/药Y 必须恢复，而不是被同秒兄弟行抢占
        assert ("爸爸", "药Y", "5mg") in final
        # 重复导入保持幂等
        assert target.import_data(backup)["medications"] == 0
    finally:
        target.close()


def test_reminder_same_origin_empty_range_keeps_single_row(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.save_profile("爸爸", {})
        source.add_reminder("复诊", "2026-12-02", note="n1", profile="妈妈")
        source.add_reminder("复诊", "2026-12-02", note="", profile="爸爸")
        done = source.add_reminder(
            "复诊", "2026-12-02", note="n1", profile="爸爸"
        )
        source.complete_reminder(done["id"])
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        for title in ("牙科", "眼科", "体检"):
            target.add_reminder(title, "2027-01-01")
        assert target.import_data(backup)["reminders"] == 3
        mama = next(
            item for item in target.list_reminders(False, "妈妈")
            if item["title"] == "复诊"
        )
        assert target.update_reminder(mama["id"], note="edited")
        # 既有行主键全部大于备份主键：不能据此判定同源并复制旧副本
        assert target.import_data(backup)["reminders"] == 0
        rows = [
            item for item in target.list_reminders(True, "妈妈")
            if item["title"] == "复诊"
        ]
        assert len(rows) == 1
        assert rows[0]["note"] == "edited"
        assert target.import_data(backup)["reminders"] == 0
    finally:
        target.close()


def _drop_checksum(backup):
    backup.pop("checksum", None)
    return backup


def test_lab_empty_time_excluded_from_group_size(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.add_lab_result("妈妈", "血红蛋白", 120, tested_at="")
        source.add_lab_result(
            "妈妈", "血红蛋白", 130, tested_at="2026-11-01T08:00",
            panel="血常规",
        )
        backup = _drop_checksum(source.export_data())
        backup["lab_results"][0]["tested_at"] = ""
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        first = target.import_data(backup)
        assert first["lab_results"] == 1
        assert first["invalid"] == 1
        row = target.list_lab_results("妈妈")[0]
        target.update_lab_result_partial(
            row["id"], value=131, tested_at="2026-11-02T08:00"
        )
        # 非法空时间行不计入组大小：编辑后的合法行不会被旧值副本覆盖
        assert target.import_data(backup)["lab_results"] == 0
        assert [
            (item["value"], item["tested_at"])
            for item in target.list_lab_results("妈妈")
        ] == [(131.0, "2026-11-02T08:00")]
    finally:
        target.close()


def test_vaccination_empty_time_excluded_from_group_size(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.add_vaccination("妈妈", "流感疫苗", 1, vaccinated_at="")
        source.add_vaccination(
            "妈妈", "流感疫苗", 2,
            vaccinated_at="2026-10-01", next_due="2027-10-01",
        )
        backup = _drop_checksum(source.export_data())
        backup["vaccinations"][0]["vaccinated_at"] = ""
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        first = target.import_data(backup)
        assert first["vaccinations"] == 1
        assert first["invalid"] == 1
        row = target.list_vaccinations("妈妈")[0]
        target.update_vaccination_partial(
            row["id"], next_due="2028-10-01",
            vaccinated_at="2026-10-02",
        )
        assert target.import_data(backup)["vaccinations"] == 0
        dues = sorted(
            str(item["next_due"] or "")
            for item in target.list_vaccinations("妈妈")
        )
        assert dues == ["2028-10-01"]
    finally:
        target.close()


# ---------------------------------------------------------------- 预约/提醒
def test_expired_auto_followup_restore_rejected(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date=_date(-10), diagnosis="甲",
        next_followup_date=_date(-3),
    )
    visit_id = visit["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊", "心内科",
        scheduled_at=_date(-3) + "T09:00",
    )
    appointment_id = appointment["id"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE appointments SET owner_visit_id = ?, note = ?"
            " WHERE id = ?",
            (visit_id, f"由就诊记录 #{visit_id} 自动创建",
             appointment_id),
        )
    store.update_appointment_partial(appointment_id, status="cancelled")
    with pytest.raises(ValueError):
        store.update_appointment_partial(
            appointment_id, status="upcoming"
        )
    refreshed = store.get_visit_record(visit_id)
    assert int(refreshed["followup_appointment_id"] or 0) == 0
    assert not refreshed["followup_scheduled"]


def test_past_reschedule_downgrades_auto_followup(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date=_date(-10), diagnosis="甲",
        next_followup_date=_date(10),
    )
    visit_id = visit["id"]
    scheduled = _schedule_followup_sync(store, visit_id)
    appointment_id = scheduled["appointment"]["id"]
    assert store.get_visit_record(visit_id)["followup_appointment_id"] > 0
    store.update_appointment_partial(
        appointment_id, scheduled_at=_date(-3) + "T09:00"
    )
    refreshed = store.get_visit_record(visit_id)
    assert int(refreshed["followup_appointment_id"] or 0) == 0
    assert not refreshed["followup_scheduled"]


def test_reminder_delete_tombstone_survives_cancel_restore(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date=_date(-10), diagnosis="甲",
        next_followup_date=_date(10),
    )
    visit_id = visit["id"]
    scheduled = _schedule_followup_sync(store, visit_id)
    appointment_id = scheduled["appointment"]["id"]
    reminder_id = scheduled["reminder"]["id"]
    assert store.delete_reminder(reminder_id) is True
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.update_appointment_partial(appointment_id, status="upcoming")
    assert store.list_reminders(False, "妈妈") == []
    refreshed = store.get_visit_record(visit_id)
    assert int(refreshed["followup_reminder_id"] or 0) == 0


def test_moved_auto_followup_restores_original_profile(store):
    """改挂到其他档案的自动复诊预约：原档案恢复副本，旧行不再挂回。"""
    store.save_profile("Bob", {})
    store.save_profile("Alice", {})
    followup_day = _date(30)
    visit = store.add_visit_record(
        "Bob", visit_date=_date(-10), diagnosis="Fracture",
        next_followup_date=followup_day,
    )
    appointment = store.add_appointment(
        "Bob", "复诊：Fracture", scheduled_at=followup_day + "T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊提醒", _date(29), profile="Bob",
        appointment_id=appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment_id=appointment["id"],
        reminder_id=reminder["id"],
    )
    backup = store.export_data()
    store.update_appointment_partial(appointment["id"], profile="Alice")
    # 模拟改挂发生在导出之后的另一秒（与真实时序无关地锁住行为）
    created = datetime.fromisoformat(
        store.get_appointment(appointment["id"])["created_at"]
    )
    later = (created + timedelta(seconds=2)).isoformat(timespec="seconds")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE appointments SET updated_at = ? WHERE id = ?",
            (later, appointment["id"]),
        )
    store.import_data(backup)
    bob_upcoming = [
        item for item in store.list_appointments("Bob", None)
        if item["status"] == "upcoming"
    ]
    # 原档案必须补回自动复诊副本，而不是把改挂到 Alice 的行吞掉
    assert len(bob_upcoming) == 1
    assert bob_upcoming[0]["id"] != appointment["id"]
    store.update_appointment_partial(appointment["id"], profile="Bob")
    store.import_data(backup)
    old_after = store.get_appointment(appointment["id"])
    assert int(old_after["owner_visit_id"] or 0) == 0
    assert "由就诊记录 #" not in str(old_after["note"] or "")
    linked = [
        item for item in store.list_appointments("Bob", None)
        if item["status"] == "upcoming"
        and int(item.get("owner_visit_id") or 0) == visit["id"]
    ]
    assert len(linked) == 1


# ---------------------------------------------------------------- 认证
def test_known_token_bypasses_shared_bucket_prelock(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    token = "a-secure-random-token-123"
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    # 单元素地址头按单跳代理追加的地址进入预锁定桶
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    api_server._known_token_proofs.clear()
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
    forwarded = {"X-Forwarded-For": "203.0.113.9"}
    try:
        # 未认证的新来源：错误令牌被预锁定，且期间不再执行令牌比较，
        # 正确令牌（无短期凭证）同样 429——不能形成全速猜解 oracle
        assert client.get(
            "/api/sessions",
            headers={"X-Medix-Token": "bad", **forwarded},
        ).status_code == 401
        assert client.get(
            "/api/sessions",
            headers={"X-Medix-Token": "bad", **forwarded},
        ).status_code == 429
        assert client.get(
            "/api/sessions",
            headers={"X-Medix-Token": token, **forwarded},
        ).status_code == 429
    finally:
        api_server._known_token_proofs.clear()
        session_store.close()


def test_authenticated_session_survives_lockout_with_proof(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    token = "a-secure-random-token-123"
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    api_server._known_token_proofs.clear()
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
    forwarded = {"X-Forwarded-For": "203.0.113.9"}
    try:
        # 已认证会话先取得短期凭证；随后共享桶被错误令牌锁满，
        # 该会话仍凭凭证直通（正确令牌不再每次裸比较、也不再连坐）
        assert client.get(
            "/api/sessions",
            headers={"X-Medix-Token": token, **forwarded},
        ).status_code == 200
        assert client.get(
            "/api/sessions",
            headers={"X-Medix-Token": "bad", **forwarded},
        ).status_code == 401
        assert client.get(
            "/api/sessions",
            headers={"X-Medix-Token": "bad", **forwarded},
        ).status_code == 429
        assert client.get(
            "/api/sessions",
            headers={"X-Medix-Token": token, **forwarded},
        ).status_code == 200
    finally:
        api_server._known_token_proofs.clear()
        session_store.close()


# ---------------------------------------------------------------- 前端
def test_frontend_takeover_and_inflight_merge():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 接管分支保留提交时的档案归属（否则可被送进另一位患者的会话）
    marker = "会话已切换，刚才的主诉未提交，请确认后重新发送"
    marker_index = source.index(marker)
    takeover = source[max(0, marker_index - 600):marker_index]
    assert "detachedComposerText = question;" in takeover
    assert "preserveDraftProfile(sendProfile, question);" in takeover
    # 档案冲突分支与相邻分支一致：合并草稿而不是整段覆盖
    conflict_start = source.index(
        "if (submitProfile && submitProfile !== sendProfile) {"
    )
    conflict = source[conflict_start:conflict_start + 1200]
    assert "restoreComposer(question);" in conflict
    assert "detachedComposerText = question;" not in conflict
    assert "preserveDraftProfile(submitProfile, question);" in conflict
    # 在途主诉与无关草稿合并保留，不再永久不可见
    restore_start = source.index("function restoreInflightQuestion(")
    restore = source[restore_start:restore_start + 3000]
    assert "上次会诊未完成，主诉已合并到输入框" in restore
    assert "preserveDraftProfile(profile, question);" in restore
    assert "上次会诊未完成；当前草稿已保留" not in restore


# ---------------------------------------------------------------- 记忆盐
def test_salt_atomic_creation_converges(tmp_path):
    from core import session_context

    target = tmp_path / "data" / ".memory_scope_salt"
    results = []
    lock = threading.Lock()

    def worker():
        value = session_context._read_or_create_salt(target)
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 8
    assert len(set(results)) == 1
    assert len(results[0]) == 64
    assert (
        target.read_text(encoding="utf-8").strip() == results[0]
    )
    # 复用已有盐
    assert session_context._read_or_create_salt(target) == results[0]


def test_memory_scope_salt_anchored_to_data_dir(tmp_path, monkeypatch):
    from core import session_context

    monkeypatch.delenv("MEDIX_MEMORY_SCOPE_SALT", raising=False)
    monkeypatch.setenv(
        "MEDIX_SESSION_DB", str(tmp_path / "dbs" / "sessions.db")
    )
    monkeypatch.setattr(session_context, "_memory_scope_salt_cache", "")
    try:
        salt = session_context._memory_scope_salt()
        path = tmp_path / "dbs" / ".memory_scope_salt"
        assert salt
        assert path.read_text(encoding="utf-8").strip() == salt
    finally:
        session_context._memory_scope_salt_cache = ""

    recorded = {}

    def fake_read_or_create(path):
        recorded["path"] = path
        return "x" * 64

    monkeypatch.setattr(
        session_context, "_read_or_create_salt", fake_read_or_create
    )
    monkeypatch.delenv("MEDIX_SESSION_DB", raising=False)
    monkeypatch.setattr(session_context, "_memory_scope_salt_cache", "")
    try:
        session_context._memory_scope_salt()
        assert recorded["path"] == (
            Path(session_context.__file__).resolve().parent.parent
            / "data" / ".memory_scope_salt"
        )
    finally:
        session_context._memory_scope_salt_cache = ""
