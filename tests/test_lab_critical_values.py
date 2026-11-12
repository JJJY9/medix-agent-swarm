"""化验危急值规则与接口集成测试。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.lab_values import (
    _analyte_index,
    _lab_item_text,
    lab_critical_alerts,
    normalize_lab_measurement,
)
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


def _at(days=0):
    return (datetime.now() + timedelta(days=days)).isoformat(
        timespec="minutes"
    )


def _alerts(item, value, unit="", tested_at=None):
    return lab_critical_alerts(item, value, unit, tested_at)


def test_thresholds_trigger_only_beyond_boundaries():
    assert _alerts("血钾", 6.6, "mmol/L")[0]["direction"] == "high"
    assert _alerts("血钾", 6.5, "mmol/L") == []
    assert _alerts("血钾", 2.4, "mmol/L")[0]["direction"] == "low"
    assert _alerts("血钾", 2.5, "mmol/L") == []

    assert _alerts("血钠", 119, "mmol/L")[0]["direction"] == "low"
    assert _alerts("血钠", 120, "mmol/L") == []
    assert _alerts("血钠", 161, "mmol/L")[0]["direction"] == "high"
    assert _alerts("血钠", 160, "mmol/L") == []

    assert _alerts("血糖", 2.7, "mmol/L")[0]["direction"] == "low"
    assert _alerts("血糖", 2.8, "mmol/L") == []
    assert _alerts("血糖", 22.3, "mmol/L")[0]["direction"] == "high"
    assert _alerts("血糖", 22.2, "mmol/L") == []

    assert _alerts("血红蛋白", 49, "g/L")[0]["direction"] == "low"
    assert _alerts("血红蛋白", 50, "g/L") == []
    assert _alerts("血红蛋白", 201, "g/L")[0]["direction"] == "high"
    assert _alerts("血红蛋白", 200, "g/L") == []

    assert _alerts("血小板", 19, "×10⁹/L")[0]["direction"] == "low"
    assert _alerts("血小板", 20, "×10⁹/L") == []
    assert _alerts("血小板", 1001, "×10⁹/L")[0]["direction"] == "high"
    assert _alerts("血小板", 1000, "×10⁹/L") == []
    assert _alerts("血小板", 150, "×10⁹/L") == []

    assert _alerts("白细胞", 0.9, "×10⁹/L")[0]["direction"] == "low"
    assert _alerts("白细胞", 1.0, "×10⁹/L") == []
    assert _alerts("白细胞", 51, "×10⁹/L")[0]["direction"] == "high"
    assert _alerts("白细胞", 50, "×10⁹/L") == []

    assert _alerts("INR", 5.1, "")[0]["direction"] == "high"
    assert _alerts("INR", 5.0, "") == []
    assert _alerts("INR", 5.1, "比值")[0]["direction"] == "high"

    assert _alerts("肌酐", 708, "μmol/L")[0]["direction"] == "high"
    assert _alerts("肌酐", 707, "μmol/L") == []
    assert _alerts("肌酐", 8.0, "mg/dL")[0]["direction"] == "high"
    assert _alerts("肌酐", 7.9, "mg/dL") == []

    assert _alerts("血钙", 1.4, "mmol/L")[0]["direction"] == "low"
    assert _alerts("血钙", 1.5, "mmol/L") == []
    assert _alerts("血钙", 3.6, "mmol/L")[0]["direction"] == "high"
    assert _alerts("血钙", 3.5, "mmol/L") == []
    assert _alerts("血钙", 14, "mg/dL") == []
    assert _alerts("血钙", 15, "mg/dL")[0]["direction"] == "high"


def test_ionized_calcium_has_its_own_thresholds():
    # 离子钙正常约 1.15–1.35 mmol/L，不能套用总钙 1.5–3.5 的阈值
    for name in ("离子钙", "游离钙", "钙离子", "iCa"):
        assert _alerts(name, 1.2, "mmol/L") == [], name
        assert _alerts(name, 0.75, "mmol/L")[0]["direction"] == "low"
        assert _alerts(name, 1.7, "mmol/L")[0]["direction"] == "high"
    assert _alerts("离子钙", 4.5, "mg/dL") == []
    assert _alerts("离子钙", 3.0, "mg/dL")[0]["direction"] == "low"


def test_units_are_converted_or_skipped():
    assert _alerts("血糖", 400, "mg/dL")[0]["direction"] == "high"
    assert _alerts("血糖", 250, "mg/dL") == []
    assert _alerts("血红蛋白", 4.9, "g/dL")[0]["direction"] == "low"
    assert _alerts("血小板", 15, "K/uL")[0]["direction"] == "low"
    assert _alerts("白细胞", 6.5, "K/uL") == []
    assert _alerts("血小板", 150000, "/uL") == []
    assert _alerts("白细胞", 900, "/uL")[0]["direction"] == "low"
    # 10³/µL 与 K/µL 等价于 ×10⁹/L，不能按 /µL 缩小 1000 倍
    for unit in ("×10³/µL", "10^3/μL", "10*3/uL", "10E3/µL"):
        assert _alerts("白细胞", 6.5, unit) == [], unit
        assert _alerts("血小板", 150, unit) == [], unit
        assert _alerts("白细胞", 0.5, unit)[0]["direction"] == "low"
    # 全角单位与上标要归一化后再匹配
    for unit in ("mmol／L", "ｍｍｏｌ／Ｌ"):
        assert _alerts("血钾", 6.8, unit)[0]["direction"] == "high"
    # 单位缺失或无法识别时不猜测
    assert _alerts("血钾", 6.8, "") == []
    assert _alerts("血钾", 6.8, "mg/dL") == []
    assert _alerts("血糖", 400, "") == []


def test_message_uses_normalized_value_and_unit():
    alert = _alerts("血糖", 400, "mg/dL")[0]
    assert "22.22 mmol/L" in alert["message"]
    alert = _alerts("白细胞", 900, "/uL")[0]
    assert "0.9 ×10⁹/L" in alert["message"]
    alert = _alerts("肌酐", 8, "mg/dL")[0]
    assert "707.2 μmol/L" in alert["message"]
    alert = _alerts("血红蛋白", 4.9, "g/dL")[0]
    assert "49 g/L" in alert["message"]
    # 文案只建议复测与就医，不给出进食/剂量等处置指令
    low_glucose = _alerts("血糖", 2.7, "mmol/L")[0]["message"]
    assert "进食" not in low_glucose
    assert "低血糖" in low_glucose
    assert "联系医生" in low_glucose


def test_aliases_and_exclusions():
    for name in ("血钾(K+)", "K", "potassium", "血清钾"):
        assert _alerts(name, 6.8, "mmol/L"), name
    for name in ("PT-INR", "INR（PT）", "国际标准化比值"):
        assert _alerts(name, 6.0, ""), name
    # 这些排除项在去掉 exclude 后会命中对应规则，用来锁定排除逻辑
    excluded = (
        ("尿K", 2.0, "mmol/L"),
        ("尿Na", 100, "mmol/L"),
        ("糖化血红蛋白", 30, "g/L"),
        ("平均红细胞血红蛋白浓度", 330, "g/L"),
        ("肌酐清除率", 800, "μmol/L"),
        ("血小板压积", 2500, "×10⁹/L"),
        ("白细胞分类", 60, "×10⁹/L"),
        ("胸水葡萄糖", 1.5, "mmol/L"),
        ("腹水葡萄糖", 1.5, "mmol/L"),
        ("脑脊液葡萄糖", 1.5, "mmol/L"),
        ("尿葡萄糖", 1.5, "mmol/L"),
        ("脑钠肽", 100, "mmol/L"),
        ("CRP", 800, "μmol/L"),
        ("钙卫蛋白", 3.6, "mmol/L"),
    )
    for name, value, unit in excluded:
        assert _alerts(name, value, unit) == [], name
    # 排除项不影响真实项目
    assert _alerts("葡萄糖", 2.0, "mmol/L")[0]["direction"] == "low"
    assert _alerts("血小板计数", 15, "×10⁹/L")[0]["direction"] == "low"


def test_stale_results_are_flagged():
    recent = _at(-10)
    fresh_alert = _alerts("血钾", 6.8, "mmol/L", recent)[0]
    assert fresh_alert["stale"] is False
    assert "超过 180 天" not in fresh_alert["message"]
    boundary = _alerts("血钾", 6.8, "mmol/L", _at(-180))[0]
    assert boundary["stale"] is False
    old_alert = _alerts("血钾", 6.8, "mmol/L", _at(-181))[0]
    assert old_alert["stale"] is True
    assert "超过 180 天" in old_alert["message"]


def test_api_marks_critical_lab_results(client_and_store):
    client, _store = client_and_store
    saved = client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "血钾", "value": 6.8,
        "unit": "mmol/L", "tested_at": _at(0),
    }).json()["lab_result"]

    assert saved["critical"] is True
    assert saved["critical_alerts"][0]["message"].startswith("血钾 6.8")

    listed = client.get(
        "/api/lab-results", params={"profile": "妈妈"}
    ).json()["lab_results"]
    assert listed[0]["critical"] is True

    updated = client.patch(
        f"/api/lab-results/{saved['id']}", json={"value": 4.0}
    ).json()["lab_result"]
    assert updated["critical"] is False
    assert updated["critical_alerts"] == []

    normal = client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "ALT", "value": 55,
        "unit": "U/L", "ref_low": 7, "ref_high": 40,
        "tested_at": _at(0),
    }).json()["lab_result"]
    assert normal["critical"] is False
    assert normal["critical_alerts"] == []
    assert normal["abnormal_high"] is True


def test_insights_family_and_dossier_include_critical_lab(client_and_store):
    client, _store = client_and_store
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "血钾", "value": 6.8,
        "unit": "mmol/L", "tested_at": _at(0),
    })

    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()["insights"]
    lab_critical = next(
        item for item in insights if item["key"] == "lab_critical"
    )
    assert lab_critical["level"] == "danger"
    assert "血钾" in lab_critical["detail"]
    assert insights[0]["level"] == "danger"

    overview = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()["profiles"]
    mom = next(item for item in overview if item["name"] == "妈妈")
    assert mom["labs"]["critical"] == 1
    assert mom["attention"]["level"] == "danger"
    assert "化验危急值" in mom["attention"]["label"]

    weekly = client.get(
        "/api/reports/weekly", params={"profile": "妈妈"}
    ).json()
    assert weekly["labs"]["critical"] == 1
    assert "血钾" in weekly["labs"]["critical_items"][0]["message"]

    timeline = client.get(
        "/api/timeline", params={"profile": "妈妈", "type": "lab"}
    ).json()["events"]
    lab_event = next(item for item in timeline if item["type"] == "lab")
    assert lab_event["critical"] is True
    assert lab_event["critical_message"].startswith("血钾")

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    )
    assert dossier.status_code == 200
    assert "（危急值）" in dossier.text

    export = client.get(
        "/api/lab-results/export.csv", params={"profile": "妈妈"}
    )
    assert "是" in export.text


def test_family_insight_labels_member_and_stale_dossier(client_and_store):
    client, _store = client_and_store
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "血钾", "value": 6.8,
        "unit": "mmol/L", "tested_at": _at(0),
    })
    client.post("/api/lab-results", json={
        "profile": "爸爸", "item": "INR", "value": 6.0,
        "unit": "", "tested_at": _at(0),
    })

    insights = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()["insights"]
    lab_critical = next(
        item for item in insights if item["key"] == "lab_critical"
    )
    assert "妈妈：" in lab_critical["detail"]
    assert "爸爸：" in lab_critical["detail"]

    client.post("/api/lab-results", json={
        "profile": "奶奶", "item": "血钾", "value": 6.8,
        "unit": "mmol/L", "tested_at": _at(-300),
    })
    dossier = client.get(
        "/api/health/dossier.md",
        params={"profile": "奶奶", "days": 365},
    )
    assert "（危急值）" in dossier.text
    assert "结果较旧" in dossier.text


def test_analyte_resolution_is_cached_across_rows():
    _analyte_index.cache_clear()
    first = _analyte_index(_lab_item_text("血钾"))
    assert first >= 0

    for _ in range(100):
        assert normalize_lab_measurement("血钾", 4.2, "mmol/L") is not None
        assert lab_critical_alerts("血钾", 7.0, "mmol/L")

    info = _analyte_index.cache_info()
    assert info.misses == 1
    assert info.hits >= 200


def test_analyte_cache_distinguishes_unknown_items():
    assert _analyte_index(_lab_item_text("未知项目")) == -1
    assert _analyte_index(_lab_item_text("")) == -1

