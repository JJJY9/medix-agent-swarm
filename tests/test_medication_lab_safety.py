"""用药与化验交叉安全提示测试。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_lab_safety import check_medication_lab_safety
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


def _lab(item, value, unit="", ref_high=None, days=0):
    return {
        "item": item,
        "value": value,
        "unit": unit,
        "ref_high": ref_high,
        "tested_at": _at(days),
    }


def _titles(medications, labs, context=None):
    result = check_medication_lab_safety(medications, labs, context)
    return [item["title"] for item in result["alerts"]]


def test_raas_blockers_with_high_potassium():
    labs = [_lab("血钾", 5.6, "mmol/L")]
    assert _titles(["培哚普利"], labs) == ["培哚普利与血钾偏高"]
    assert _titles(["培哚普利"], [_lab("血钾", 5.0, "mmol/L")]) == []
    assert _titles(["培哚普利"], [_lab("血钾", 5.6, "")]) == []
    assert _titles(["维生素 D"], labs) == []


def test_potassium_sparing_diuretic_with_high_potassium():
    titles = _titles(["螺内酯"], [_lab("血钾", 5.8, "mmol/L")])
    assert titles == ["螺内酯与血钾偏高"]


def test_raas_blockers_with_reduced_egfr():
    labs = [_lab("eGFR", 52, "mL/min/1.73m²")]
    titles = _titles(["缬沙坦"], labs)
    assert titles == ["缬沙坦与肾功能下降"]
    assert _titles(["缬沙坦"], [_lab("eGFR", 70, "mL/min/1.73m²")]) == []


def test_egfr_is_estimated_from_creatinine_when_missing():
    labs = [_lab("肌酐", 150, "μmol/L")]
    context = {"age": "70", "sex": "女"}
    titles = _titles(["缬沙坦"], labs, context)
    assert titles == ["缬沙坦与肾功能下降"]
    # 缺少年龄/性别时不猜测
    assert _titles(["缬沙坦"], labs, {"sex": "女"}) == []


def test_metformin_with_low_egfr():
    labs = [_lab("eGFR", 40, "mL/min/1.73m²")]
    assert _titles(["二甲双胍"], labs) == ["二甲双胍与 eGFR 下降"]
    assert _titles(["二甲双胍"], [_lab("eGFR", 50, "mL/min/1.73m²")]) == []


def test_statin_with_raised_alt():
    labs = [_lab("ALT", 130, "U/L", ref_high=40)]
    assert _titles(["阿托伐他汀"], labs) == ["阿托伐他汀与转氨酶升高"]
    assert _titles(
        ["阿托伐他汀"], [_lab("ALT", 130, "U/L")]
    ) == []
    assert _titles(
        ["阿托伐他汀"], [_lab("ALT", 110, "U/L", ref_high=40)]
    ) == []
    # μkat/L 换算：数值与参考上限都按同一系数换算
    titles = _titles(
        ["阿托伐他汀"],
        [_lab("ALT", 2.2, "μkat/L", ref_high=0.5)],
    )
    assert titles == ["阿托伐他汀与转氨酶升高"]
    # AST/ALT 比值不是转氨酶绝对值
    assert _titles(
        ["阿托伐他汀"],
        [_lab("AST/ALT比值", 3, "U/L", ref_high=1)],
    ) == []


def test_warfarin_with_raised_inr():
    labs = [_lab("INR", 3.5, "")]
    assert _titles(["华法林"], labs) == ["华法林与 INR 升高"]
    assert _titles(["华法林"], [_lab("INR", 3.0, "")]) == []


def test_digoxin_level():
    assert _titles(
        ["地高辛"], [_lab("地高辛浓度", 2.5, "ng/mL")]
    ) == ["地高辛与地高辛浓度偏高"]
    assert _titles(
        ["地高辛"], [_lab("地高辛", 2500, "ng/L")]
    ) == ["地高辛与地高辛浓度偏高"]
    assert _titles(
        ["地高辛"], [_lab("地高辛浓度", 1.5, "ng/mL")]
    ) == []


def test_diuretic_with_electrolyte_loss():
    titles = _titles(
        ["呋塞米"],
        [_lab("血钾", 3.2, "mmol/L"), _lab("血钠", 130, "mmol/L")],
    )
    assert titles == ["呋塞米与电解质偏低"]
    assert _titles(
        ["氢氯噻嗪"], [_lab("血钾", 3.2, "mmol/L")]
    ) == ["氢氯噻嗪与电解质偏低"]
    assert _titles(
        ["呋塞米"], [_lab("血钾", 4.0, "mmol/L")]
    ) == []


def test_latest_measurement_wins():
    labs = [
        _lab("血钾", 6.0, "mmol/L", days=-10),
        _lab("血钾", 4.0, "mmol/L", days=-1),
    ]
    assert _titles(["缬沙坦"], labs) == []


def test_lab_safety_matches_each_medication_separately():
    labs = [_lab("血钾", 5.6, "mmol/L")]
    # 「无极膏」的「无」不应把后面的培哚普利当成未用药
    assert _titles(["无极膏", "培哚普利"], labs) == [
        "培哚普利与血钾偏高"
    ]


def test_empty_inputs_return_no_alerts():
    assert check_medication_lab_safety([], [])["alerts"] == []
    assert check_medication_lab_safety(["缬沙坦"], [])["alerts"] == []


def test_api_medication_safety_includes_lab_alerts(client_and_store):
    client, _store = client_and_store
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "血钾", "value": 5.8,
        "unit": "mmol/L", "tested_at": _at(0),
    })
    response = client.post("/api/medications", json={
        "profile": "妈妈", "name": "培哚普利",
    })
    assert response.status_code == 200
    safety = response.json()["safety"]
    assert safety["level"] == "warning"
    assert any("血钾" in row["title"] for row in safety["alerts"])

    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()["insights"]
    safety_insight = next(
        item for item in insights if item["key"] == "medication_safety"
    )
    assert safety_insight["level"] == "warning"
    assert "血钾" in safety_insight["detail"]


def test_api_uses_profile_age_and_sex_for_egfr(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={
        "name": "奶奶",
        "context": {"age": "70", "sex": "女"},
    })
    client.post("/api/lab-results", json={
        "profile": "奶奶", "item": "肌酐", "value": 150,
        "unit": "μmol/L", "tested_at": _at(0),
    })
    response = client.post("/api/medications", json={
        "profile": "奶奶", "name": "二甲双胍",
    })
    safety = response.json()["safety"]
    assert any(
        "eGFR" in row["title"] for row in safety["alerts"]
    ), safety


def test_api_ignores_labs_older_than_180_days(client_and_store):
    client, _store = client_and_store
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "血钾", "value": 6.8,
        "unit": "mmol/L", "tested_at": _at(-200),
    })
    response = client.post("/api/medications", json={
        "profile": "妈妈", "name": "缬沙坦",
    })
    safety = response.json()["safety"]
    assert not any(
        "血钾" in row["title"] for row in safety["alerts"]
    ), safety


def test_threshold_equalities_are_strict():
    assert _titles(["缬沙坦"], [_lab("eGFR", 60, "mL/min/1.73m²")]) == []
    assert _titles(["缬沙坦"], [_lab("eGFR", 59.9, "mL/min/1.73m²")])
    assert _titles(["二甲双胍"], [_lab("eGFR", 45, "mL/min/1.73m²")]) == []
    assert _titles(["二甲双胍"], [_lab("eGFR", 44.9, "mL/min/1.73m²")])
    assert _titles(
        ["阿托伐他汀"], [_lab("ALT", 120, "U/L", ref_high=40)]
    ) == []
    assert _titles(
        ["阿托伐他汀"], [_lab("ALT", 121, "U/L", ref_high=40)]
    )
    assert _titles(["地高辛"], [_lab("地高辛浓度", 2.0, "ng/mL")]) == []
    assert _titles(["地高辛"], [_lab("地高辛浓度", 2.1, "ng/mL")])
    assert _titles(["华法林"], [_lab("INR", 3.0, "")]) == []
    assert _titles(["华法林"], [_lab("INR", 3.1, "")])
    assert _titles(["缬沙坦"], [_lab("血钾", 5.0, "mmol/L")]) == []
    assert _titles(["缬沙坦"], [_lab("血钾", 5.1, "mmol/L")])


def test_alt_reference_high_in_same_unit():
    # 参考上限与数值同为 μkat/L：正常范围不提示
    assert _titles(
        ["阿托伐他汀"], [_lab("ALT", 0.45, "μkat/L", ref_high=0.58)]
    ) == []
    # 3.2 μkat/L = 192 U/L，参考上限 1.0 μkat/L = 60 U/L，超过 3 倍
    assert _titles(
        ["阿托伐他汀"], [_lab("ALT", 3.2, "μkat/L", ref_high=1.0)]
    ) == ["阿托伐他汀与转氨酶升高"]


def test_alt_ratio_spellings_are_ignored():
    for item in (
        "AST/ALT", "ALT/AST", "AST/ALT比值", "ALT/AST ratio",
        "谷草转氨酶/谷丙转氨酶", "ALT/AST比率",
    ):
        assert _titles(
            ["阿托伐他汀"], [_lab(item, 3.5, "U/L", ref_high=1.0)]
        ) == [], item


def test_statins_cover_all_common_members():
    labs = [_lab("ALT", 200, "U/L", ref_high=40)]
    for name in ("普伐他汀", "氟伐他汀", "匹伐他汀", "瑞舒伐他汀"):
        assert _titles([name], labs) == [f"{name}与转氨酶升高"], name


def test_potassium_sparing_covers_mra():
    labs = [_lab("血钾", 5.8, "mmol/L")]
    for name in ("依普利酮", "非奈利酮"):
        assert _titles([name], labs) == [f"{name}与血钾偏高"], name


def test_egfr_prefers_most_recent_measurement():
    labs = [
        _lab("eGFR", 55, "mL/min/1.73m²", days=-150),
        _lab("肌酐", 70, "μmol/L", days=-1),
    ]
    context = {"age": "40", "sex": "男"}
    assert _titles(["缬沙坦"], labs, context) == []
    assert _titles(
        ["缬沙坦"],
        [_lab("eGFR", 55, "mL/min/1.73m²", days=-100)],
        context,
    ) == ["缬沙坦与肾功能下降"]


def test_egfr_detail_includes_date_and_source():
    labs = [_lab("eGFR", 52, "mL/min/1.73m²", days=-3)]
    result = check_medication_lab_safety(["缬沙坦"], labs, {})
    detail = result["alerts"][0]["detail"]
    assert "化验" in detail
    assert _at(-3)[:10] in detail


def test_egfr_uses_age_and_sex():
    labs = [_lab("肌酐", 120, "μmol/L")]
    male = _titles(["缬沙坦"], labs, {"age": "35", "sex": "男"})
    female = _titles(["缬沙坦"], labs, {"age": "35", "sex": "女"})
    assert male == []
    assert female == ["缬沙坦与肾功能下降"]


def test_zero_creatinine_does_not_crash():
    labs = [_lab("肌酐", 0, "μmol/L")]
    context = {"age": "70", "sex": "女"}
    assert _titles(["二甲双胍"], labs, context) == []


def test_same_timestamp_keeps_newest_first():
    # 接口按 tested_at DESC, id DESC 返回：新录入的 4.2 在前，旧的 5.9 在后
    stamp = _at(0)
    labs = [
        {"item": "血钾", "value": 4.2, "unit": "mmol/L", "tested_at": stamp},
        {"item": "血钾", "value": 5.9, "unit": "mmol/L", "tested_at": stamp},
    ]
    assert _titles(["缬沙坦"], labs) == []


def test_panel_specimen_excludes_urine_electrolytes():
    assert _titles(
        ["缬沙坦"],
        [{
            "item": "钾", "panel": "尿电解质", "value": 60,
            "unit": "mmol/L", "tested_at": _at(0),
        }],
    ) == []
    assert _titles(
        ["呋塞米"],
        [{
            "item": "钠", "panel": "尿电解质", "value": 40,
            "unit": "mmol/L", "tested_at": _at(0),
        }],
    ) == []


def test_diuretic_sodium_only_and_boundaries():
    assert _titles(
        ["呋塞米"],
        [_lab("血钾", 4.0, "mmol/L"), _lab("血钠", 130, "mmol/L")],
    ) == ["呋塞米与电解质偏低"]
    assert _titles(["呋塞米"], [_lab("血钠", 135, "mmol/L")]) == []
    assert _titles(["呋塞米"], [_lab("血钾", 3.5, "mmol/L")]) == []
    assert _titles(["呋塞米"], [_lab("血钾", 3.4, "mmol/L")])


def test_digoxin_units():
    assert _titles(
        ["地高辛"], [_lab("地高辛", 2.5, "μg/L")]
    ) == ["地高辛与地高辛浓度偏高"]
    assert _titles(["地高辛"], [_lab("地高辛", 1500, "ng/L")]) == []


def test_api_zero_creatinine_does_not_break_medication_save(
    client_and_store,
):
    client, _store = client_and_store
    client.post("/api/profiles", json={
        "name": "奶奶",
        "context": {"age": "70", "sex": "女"},
    })
    client.post("/api/lab-results", json={
        "profile": "奶奶", "item": "肌酐", "value": 0,
        "unit": "μmol/L", "tested_at": _at(0),
    })
    response = client.post("/api/medications", json={
        "profile": "奶奶", "name": "二甲双胍",
    })
    assert response.status_code == 200
    assert not any(
        "eGFR" in row["title"] for row in response.json()["safety"]["alerts"]
    )
    insights = client.get(
        "/api/health/insights", params={"profile": "奶奶", "days": 30}
    )
    assert insights.status_code == 200


def test_api_preserves_existing_safety_alerts(client_and_store):
    client, _store = client_and_store
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "培哚普利",
    })
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "青霉素",
        "severity": "severe", "kind": "drug",
    })
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "血钾", "value": 5.8,
        "unit": "mmol/L", "tested_at": _at(0),
    })
    safety = client.post("/api/medications", json={
        "profile": "妈妈", "name": "阿莫西林",
    }).json()["safety"]
    titles = [row["title"] for row in safety["alerts"]]
    assert any("血钾" in title for title in titles), titles
    assert any("过敏" in title for title in titles), titles


def test_api_lab_safety_without_profile(client_and_store):
    client, _store = client_and_store
    client.post("/api/lab-results", json={
        "profile": "", "item": "血钾", "value": 5.8,
        "unit": "mmol/L", "tested_at": _at(0),
    })
    safety = client.post("/api/medications", json={
        "profile": "", "name": "培哚普利",
    }).json()["safety"]
    assert any("血钾" in row["title"] for row in safety["alerts"])


def test_family_overview_attention_includes_medication_lab(client_and_store):
    client, _store = client_and_store
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "eGFR", "value": 44,
        "unit": "mL/min/1.73m²", "tested_at": _at(0),
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "二甲双胍",
    })
    overview = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()["profiles"]
    mom = next(item for item in overview if item["name"] == "妈妈")
    assert mom["medication_lab_safety"]["count"] >= 1
    assert "用药安全" in mom["attention"]["label"]


def test_single_char_analyte_with_panel_still_matches():
    lab = {**_lab("钾", 7.0, "mmol/L"), "panel": "电解质"}
    titles = _titles(["缬沙坦"], [lab])

    assert "缬沙坦与血钾偏高" in titles


def test_single_char_analyte_does_not_match_urine_panel():
    lab = {**_lab("钾", 7.0, "mmol/L"), "panel": "尿电解质"}
    assert _titles(["缬沙坦"], [lab]) == []
