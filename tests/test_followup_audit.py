"""审计后续修复的回归测试：过敏句式、否定词、提醒时区、审计筛选与批量上限。"""
from datetime import datetime

import pytest

from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags
from memory.session_store import SessionStore
from research.evidence_synthesizer import EvidenceSynthesizer


@pytest.fixture
def store(tmp_path):
    instance = SessionStore(str(tmp_path / "followup.db"))
    yield instance
    instance.close()


def test_allergy_without_dui_still_detected():
    for question in (
        "我青霉素过敏，能吃阿莫西林吗？",
        "过敏史：青霉素。今天能吃阿莫西林吗？",
    ):
        result = check_medication_safety(question)
        titles = [alert["title"] for alert in result["alerts"]]
        assert any("青霉素类过敏史" in title for title in titles), question


def test_negated_allergy_still_ignored():
    result = check_medication_safety("我没有阿司匹林过敏，能吃布洛芬吗？")
    titles = [alert["title"] for alert in result["alerts"]]
    assert not any("过敏史" in title for title in titles)


def test_triage_understands_more_negation_phrases():
    for text in (
        "无明确胸痛，但有点胸闷",
        "未发生胸痛",
        "未诉胸痛，仅有咳嗽",
    ):
        assert detect_red_flags(text)["level"] == "none", text

    assert detect_red_flags("无诱因突发胸痛")["level"] == "emergency"


def test_repeat_until_with_timezone_does_not_crash(store):
    reminder = store.add_reminder(
        "服药", "2026-11-09T09:00",
        repeat_days=1, repeat_until="2026-11-10T08:00:00+08:00",
    )

    result = store.complete_reminder_with_repeat(reminder["id"])

    assert result["completed"] is True
    assert result["next"] is None


def test_conflicts_recognize_no_conflict_phrasings():
    synthesizer = EvidenceSynthesizer.__new__(EvidenceSynthesizer)

    for text in ("未发现明显的信息冲突", "暂无信息冲突"):
        report = synthesizer._parse_response(
            "问题", f"【信息冲突】\n{text}。\n【综合总结】\n无", [], []
        )
        assert report.conflicts == [], text


def test_usage_by_session_handles_large_id_lists(store):
    ids = [f"session-{index}" for index in range(40000)]

    assert store.usage_by_session(ids) == {}


def test_audit_end_minute_precision_includes_record(store, monkeypatch):
    monkeypatch.setattr(
        store, "_now", lambda: "2026-03-20T11:30:00"
    )
    store.log_action("session.export", "t", "d")

    included = store.list_audit_log(end="2026-03-20T11:30")
    excluded = store.list_audit_log(end="2026-03-20T11:29")

    assert len(included) == 1
    assert excluded == []


def test_audit_keyword_escapes_like_wildcards(store):
    store.log_action("session.export", "t", "进度 50%")
    store.log_action("session.import", "t", "普通记录")

    percent = store.list_audit_log(keyword="%")
    underscore = store.list_audit_log(keyword="_")

    assert len(percent) == 1
    assert percent[0]["action"] == "session.export"
    assert underscore == []


def test_archive_keeps_favorite_and_tagged_sessions(store):
    store.save_exchange("plain", "旧问题", "旧答复")
    store.save_exchange("fav", "收藏问题", "收藏答复")
    store.save_exchange("tagged", "标签问题", "标签答复")
    store.set_favorite("fav", True)
    store.set_tags("tagged", ["复诊"])
    with store._conn:
        store._conn.execute(
            "UPDATE sessions SET updated_at = '2020-01-01T00:00'"
        )

    archived = store.archive_older_than(30)

    assert archived == 1
    items = {item["id"]: item for item in store.list_sessions(limit=10)}
    assert items["plain"]["archived"] is True
    assert items["fav"]["archived"] is False
    assert items["tagged"]["archived"] is False


def test_list_tags_merges_case_variants(store):
    store.save_exchange("s1", "问题一", "答复一")
    store.save_exchange("s2", "问题二", "答复二")
    store.set_tags("s1", ["HTN"])
    store.set_tags("s2", ["htn"])

    tags = store.list_tags()

    assert len(tags) == 1
    assert tags[0]["count"] == 2
