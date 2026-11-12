"""健康搜索 SQL 预过滤的派生字段等价性测试。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _at(days_ago=1):
    return (datetime.now() - timedelta(days=days_ago)).replace(
        hour=9, minute=0, second=0, microsecond=0
    ).isoformat(timespec="minutes")


@pytest.fixture
def search_data(client_and_store):
    client, store = client_and_store
    store.add_metric(
        "妈妈", "bp", 130, 85, measured_at=_at(1), context="morning"
    )
    store.add_symptom("妈妈", "头痛", 5, recorded_at=_at(1), note="起床后")
    store.add_medication("妈妈", "氨氯地平", "5 mg", "每日一次")
    store.add_lab_result(
        "妈妈", "ALT", 80, "U/L", 7, 40, tested_at=_at(1),
    )
    store.add_vaccination("妈妈", "HPV", 2, vaccinated_at=_at(30))
    store.add_appointment(
        "妈妈", "口腔复查", department="口腔科", scheduled_at=_at(0),
        status="upcoming",
    )
    store.add_care_item(
        "妈妈", "复查血压", 30, last_done_at=_at(40), active=False,
    )
    store.add_visit_record(
        "妈妈", visit_date=_at(2), department="心内科",
        diagnosis="高血压", prescription="氨氯地平 5mg",
    )
    store.add_allergy(
        "妈妈", "青霉素", "过敏性休克", "severe", noted_at=_at(300),
    )
    store.add_condition(
        "妈妈", "高血压", "I10", diagnosed_at=_at(200),
        status="controlled", severity="moderate",
    )
    reminder = store.add_reminder("复查血压", _at(0), profile="妈妈")
    store.complete_reminder(reminder["id"])
    return client


def _search(client, query):
    response = client.get("/api/health/search", params={"q": query})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("query", "type_key"),
    [
        ("血压", "metric"),          # 指标中文名（派生）
        ("晨起", "metric"),          # 情境中文名（派生）
        ("在用", "medication"),      # 状态标签（派生）
        ("偏高", "lab"),             # 偏高标记（派生）
        ("40", "lab"),               # 参考范围数字（派生）
        ("待就诊", "appointment"),   # 预约状态（派生）
        ("已停用", "care"),          # 复查状态（派生）
        ("重度", "allergy"),         # 过敏程度（派生）
        ("已控制", "condition"),     # 慢病状态（派生）
        ("已完成", "reminder"),      # 提醒状态（派生）
    ],
)
def test_search_matches_derived_fields(search_data, query, type_key):
    data = _search(search_data, query)
    assert data["counts"][type_key] >= 1, data


@pytest.mark.parametrize(
    ("query", "type_key", "title_part"),
    [
        ("氨氯地平", "medication", "氨氯地平"),
        ("alt", "lab", "ALT"),
        ("hpv", "vaccination", "HPV"),
        ("口腔科", "appointment", "口腔复查"),
        ("起床后", "symptom", "头痛"),
        ("心内科", "visit", "高血压"),
        ("青霉素", "allergy", "青霉素"),
        ("高血压", "condition", "高血压"),
    ],
)
def test_search_matches_stored_text(
    search_data, query, type_key, title_part
):
    data = _search(search_data, query)
    titles = [item["title"] for item in data["results"]]
    assert any(title_part in title for title in titles), data


def test_search_visit_matches_prescription(search_data):
    data = _search(search_data, "氨氯地平 5mg")
    assert data["counts"]["visit"] >= 1


@pytest.mark.parametrize(
    ("type_key", "query", "expected"),
    [
        ("metric", "血压", "血压"),
        ("metric", "bp", "bp"),
        ("metric", "晨起", "晨起"),
        ("medication", "在用", ""),
        ("medication", "氨氯", "氨氯"),
        ("lab", "偏高", ""),
        ("lab", "40", ""),
        ("lab", "alt", "alt"),
        ("vaccination", "第2剂", ""),
        ("vaccination", "hpv", "hpv"),
        ("appointment", "待就诊", ""),
        ("appointment", "口腔", "口腔"),
        ("care", "已停用", ""),
        ("care", "复查", "复查"),
        ("allergy", "重度", ""),
        ("allergy", "青霉素", "青霉素"),
        ("condition", "已控制", ""),
        ("condition", "高血压", "高血压"),
        ("reminder", "已完成", ""),
        ("reminder", "复查", "复查"),
        ("symptom", "头痛", "头痛"),
        ("visit", "心内科", "心内科"),
        ("attachment", "报告", ""),
    ],
)
def test_prefilter_is_only_pushed_down_when_safe(
    type_key, query, expected
):
    assert api_server._health_search_prefilter(type_key, query) == expected


def test_prefilter_results_equal_unfiltered_search(search_data, monkeypatch):
    queries = [
        "血压", "晨起", "bp", "在用", "氨氯", "偏高", "40", "alt",
        "待就诊", "口腔", "已停用", "复查", "重度", "青霉素", "已控制",
        "高血压", "已完成", "头痛", "心内科", "氨氯地平 5mg", "不存在",
        "7–40", "起床后", "I10",
    ]
    original = api_server._health_search_prefilter
    original_metric_filters = api_server._health_search_metric_filters
    monkeypatch.setattr(api_server, "_health_search_prefilter", lambda t, k: "")
    monkeypatch.setattr(
        api_server, "_health_search_metric_filters", lambda k: ([], [])
    )
    baseline = {query: _search(search_data, query) for query in queries}

    monkeypatch.setattr(
        api_server, "_health_search_prefilter", original
    )
    monkeypatch.setattr(
        api_server, "_health_search_metric_filters", original_metric_filters
    )
    for query in queries:
        assert _search(search_data, query) == baseline[query], query

