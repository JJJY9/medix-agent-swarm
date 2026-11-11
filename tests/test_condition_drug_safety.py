"""慢病与用药风险提示测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.condition_drug_safety import check_condition_drug_safety
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


def _titles(medications, conditions):
    return [
        item["title"]
        for item in check_condition_drug_safety(
            medications, conditions
        )["alerts"]
    ]


def test_asthma_and_nonselective_beta_blocker():
    titles = _titles(["普萘洛尔"], ["支气管哮喘"])
    assert titles == ["哮喘/慢阻肺与非选择性β受体阻滞剂：普萘洛尔"]
    assert _titles(["普萘洛尔"], ["高血压"]) == []
    # 选择性β受体阻滞剂不在规则内
    assert _titles(["美托洛尔"], ["哮喘"]) == []


def test_copd_alias_and_condition_dicts():
    titles = _titles(["卡维地洛"], [{"name": "慢阻肺"}])
    assert titles == ["哮喘/慢阻肺与非选择性β受体阻滞剂：卡维地洛"]
    assert _titles(["噻吗洛尔"], [{"name": "COPD"}]) == [
        "哮喘/慢阻肺与非选择性β受体阻滞剂：噻吗洛尔"
    ]
    # 全角英文同样归一化匹配
    assert _titles(["噻吗洛尔"], ["ＣＯＰＤ"]) == [
        "哮喘/慢阻肺与非选择性β受体阻滞剂：噻吗洛尔"
    ]


def test_renal_and_heart_failure_spellings():
    for name in ("慢性肾功能衰竭", "慢性肾衰", "肾功能衰竭"):
        assert _titles(["布洛芬"], [name]) == [
            "肾功能不全与 NSAIDs：布洛芬"
        ], name
    for name in ("心功能不全", "心功能衰竭"):
        assert _titles(["布洛芬"], [name]) == [
            "心力衰竭与 NSAIDs：布洛芬"
        ], name


def test_peptic_ulcer_with_nsaids_or_anticoagulants():
    for name in ("胃溃疡", "十二指肠球部溃疡", "复合性溃疡", "消化道溃疡"):
        assert _titles(["布洛芬"], [name]) == [
            "溃疡/消化道出血与出血风险药物：布洛芬"
        ], name
    # 阿司匹林同时属于 NSAIDs 与抗凝抗血小板组，只出现一次
    titles = _titles(["阿司匹林"], ["消化道出血"])
    assert titles == ["溃疡/消化道出血与出血风险药物：阿司匹林"]
    # 依诺肝素不应再被包含它的短名「肝素」重复列出
    assert _titles(["依诺肝素"], ["胃溃疡"]) == [
        "溃疡/消化道出血与出血风险药物：依诺肝素"
    ]
    assert _titles(["对乙酰氨基酚"], ["胃溃疡"]) == []


def test_ckd_and_heart_failure_with_nsaids():
    assert _titles(["萘普生"], ["慢性肾脏病"]) == [
        "肾功能不全与 NSAIDs：萘普生"
    ]
    assert _titles(["布洛芬"], ["心力衰竭"]) == [
        "心力衰竭与 NSAIDs：布洛芬"
    ]
    assert _titles(["布洛芬"], ["高血压"]) == []


def test_cirrhosis_with_nsaids_or_benzodiazepines():
    assert _titles(["双氯芬酸"], ["肝硬化"]) == [
        "肝硬化与镇静/出血风险药物：双氯芬酸"
    ]
    assert _titles(["地西泮"], ["肝硬化"]) == [
        "肝硬化与镇静/出血风险药物：地西泮"
    ]


def test_glaucoma_and_bph_with_anticholinergics():
    assert _titles(["阿托品"], ["青光眼"]) == [
        "青光眼与抗胆碱能药物：阿托品"
    ]
    assert _titles(["托特罗定"], ["前列腺增生"]) == [
        "前列腺增生与抗胆碱能药物：托特罗定"
    ]
    assert _titles(["苯海拉明"], ["青光眼"]) == [
        "青光眼与抗胆碱能药物：苯海拉明"
    ]
    assert _titles(["氯苯那敏"], ["前列腺增生"]) == [
        "前列腺增生与抗胆碱能药物：氯苯那敏"
    ]
    assert _titles(["扑尔敏"], ["前列腺增生"]) == [
        "前列腺增生与抗胆碱能药物：氯苯那敏"
    ]
    assert _titles(["奥昔布宁"], ["青光眼", "前列腺肥大"]) == [
        "青光眼与抗胆碱能药物：奥昔布宁",
        "前列腺增生与抗胆碱能药物：奥昔布宁",
    ]


def test_open_angle_glaucoma_is_not_flagged():
    for name in ("开角型青光眼", "原发性开角型青光眼", "open-angle glaucoma"):
        assert _titles(["奥昔布宁"], [name]) == [], name


def test_diabetes_with_systemic_corticosteroids():
    assert _titles(["泼尼松"], ["2型糖尿病"]) == [
        "糖尿病与全身糖皮质激素：泼尼松"
    ]
    assert _titles(["泼尼松片"], ["糖尿病"]) == [
        "糖尿病与全身糖皮质激素：泼尼松"
    ]
    assert _titles(["氢化可的松"], ["糖尿病"]) == [
        "糖尿病与全身糖皮质激素：氢化可的松"
    ]
    for topical in (
        "妥布霉素地塞米松滴眼液", "醋酸泼尼松龙滴眼液",
        "氢化可的松乳膏", "倍他米松乳膏",
    ):
        assert _titles([topical], ["糖尿病"]) == [], topical
    assert _titles(["胰岛素"], ["糖尿病"]) == []


def test_alert_copy_and_max_alerts():
    result = check_condition_drug_safety(["普萘洛尔"], ["哮喘"])
    assert result["alerts"][0]["severity"] == "warning"
    assert "请与开药医生或药师核对" in result["alerts"][0]["detail"]

    medications = ["普萘洛尔", "布洛芬", "地西泮", "阿托品", "泼尼松"]
    conditions = [
        "哮喘", "胃溃疡", "慢性肾脏病", "心衰", "肝硬化",
        "青光眼", "前列腺增生", "糖尿病",
    ]
    alerts = check_condition_drug_safety(
        medications, conditions
    )["alerts"]
    assert len(alerts) == 8
    assert all(item["severity"] == "warning" for item in alerts)
    assert all(
        "请与开药医生或药师核对" in item["detail"]
        for item in alerts
    )


def test_drug_matching_is_per_medication():
    # 「无极膏」的「无」不应把后面的普萘洛尔当成未用药
    assert _titles(["无极膏", "普萘洛尔"], ["哮喘"]) == [
        "哮喘/慢阻肺与非选择性β受体阻滞剂：普萘洛尔"
    ]


def test_negated_condition_names_are_ignored():
    for name in (
        "无哮喘", "没有哮喘", "否认2型糖尿病", "无活动性胃溃疡",
        "未发现心衰", "No asthma", "asthma resolved",
    ):
        assert _titles(["普萘洛尔", "布洛芬", "泼尼松"], [name]) == [], name


def test_cured_or_family_history_conditions_are_ignored():
    for name in ("哮喘已治愈", "哮喘家族史", "胃溃疡已恢复"):
        assert _titles(["普萘洛尔", "布洛芬"], [name]) == [], name
    # 「未治愈」仍按现患处理
    assert _titles(["普萘洛尔"], ["未治愈的哮喘"]) == [
        "哮喘/慢阻肺与非选择性β受体阻滞剂：普萘洛尔"
    ]


def test_empty_inputs_return_no_alerts():
    assert check_condition_drug_safety([], [])["alerts"] == []
    assert check_condition_drug_safety(["泼尼松"], [])["alerts"] == []


def test_api_medication_safety_includes_condition_alerts(client_and_store):
    client, _store = client_and_store
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "哮喘", "status": "active",
    })
    response = client.post("/api/medications", json={
        "profile": "妈妈", "name": "普萘洛尔",
    })
    safety = response.json()["safety"]
    assert safety["level"] == "warning"
    assert any(
        "哮喘" in row["title"] for row in safety["alerts"]
    ), safety

    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()["insights"]
    safety_insight = next(
        item for item in insights if item["key"] == "medication_safety"
    )
    assert "哮喘" in safety_insight["detail"]
    assert "慢病用药 1 条" in safety_insight["detail"]


def test_api_controlled_condition_still_counts(client_and_store):
    client, _store = client_and_store
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "哮喘", "status": "controlled",
    })
    safety = client.post("/api/medications", json={
        "profile": "妈妈", "name": "普萘洛尔",
    }).json()["safety"]
    assert any(
        "哮喘" in row["title"] for row in safety["alerts"]
    ), safety


def test_family_overview_includes_condition_alerts(client_and_store):
    client, _store = client_and_store
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "哮喘", "status": "active",
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "普萘洛尔",
    })
    mom = next(
        item for item in client.get(
            "/api/family/overview", params={"days": 7}
        ).json()["profiles"]
        if item["name"] == "妈妈"
    )
    assert mom["medication_lab_safety"]["condition_count"] == 1
    assert "用药安全" in mom["attention"]["label"]


def test_api_ignores_resolved_conditions(client_and_store):
    client, _store = client_and_store
    client.post("/api/conditions", json={
        "profile": "妈妈", "name": "哮喘", "status": "resolved",
    })
    safety = client.post("/api/medications", json={
        "profile": "妈妈", "name": "普萘洛尔",
    }).json()["safety"]
    assert not any(
        "哮喘" in row["title"] for row in safety["alerts"]
    ), safety
