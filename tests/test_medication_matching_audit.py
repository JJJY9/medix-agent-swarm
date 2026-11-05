"""用药匹配与临床计算器边界的回归测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
from core.clinical_tools import calculate_audit_c, calculate_egfr
from core.medication_safety import check_medication_safety


def _titles(text, context=None):
    return [alert["title"] for alert in check_medication_safety(text, context)["alerts"]]


def test_sulfa_allergy_does_not_cross_match_macrolides():
    titles = _titles("我吃阿奇霉素，对磺胺过敏")

    assert not any("磺胺类过敏史" in title for title in titles)


def test_sulfa_allergy_matches_sulfonamide_drug():
    titles = _titles("正在吃复方新诺明，对磺胺过敏")

    assert any("磺胺类过敏史" in title for title in titles)


def test_penicillin_allergy_recognizes_amoxicillin():
    titles = _titles("我对阿莫西林过敏，能吃头孢吗？")

    assert any("青霉素类过敏史" in title for title in titles)

    titles = _titles("能吃头孢吗", {"allergies": "阿莫西林"})

    assert any("青霉素类过敏史" in title for title in titles)


def test_allergy_only_drug_is_not_treated_as_current_medication():
    titles = _titles("我对阿司匹林过敏，正在吃华法林")

    assert not any("华法林与非甾体抗炎药" in title for title in titles)


def test_advice_against_drug_is_not_current_use():
    titles = _titles("不要吃阿司匹林，正在吃华法林")

    assert not any("华法林与非甾体抗炎药" in title for title in titles)


def test_structured_allergy_negation_is_ignored():
    titles = _titles("能吃阿莫西林吗", {"allergies": "否认青霉素过敏"})

    assert not any("青霉素类过敏史" in title for title in titles)


def test_short_alias_requires_medication_context():
    titles = _titles("手术后用吗啡止痛，现在情绪安定下来了")

    assert not any("阿片类与苯二氮䓬类" in title for title in titles)

    titles = _titles("我吃安定，也用吗啡止痛")

    assert any("阿片类与苯二氮䓬类" in title for title in titles)

    titles = _titles("吗啡缓解疼痛，也在吃安定")

    assert any("阿片类与苯二氮䓬类" in title for title in titles)


def test_ascii_aliases_use_word_boundaries():
    titles = _titles("正在吃格列美脲，得了swine flu")
    assert not any("磺脲类降糖药与酒精" in title for title in titles)

    titles = _titles("正在吃格列美脲，昨晚喝了红酒")
    assert any("磺脲类降糖药与酒精" in title for title in titles)

    titles = _titles("In contrast, I take metformin daily")
    assert not any("二甲双胍与含碘造影剂" in title for title in titles)


def test_negated_contrast_is_not_current_use():
    titles = _titles("做CT不用造影剂，我在吃二甲双胍")
    assert not any("二甲双胍与含碘造影剂" in title for title in titles)

    titles = _titles("做增强CT，正在吃二甲双胍")
    assert any("二甲双胍与含碘造影剂" in title for title in titles)


def test_english_mineral_aliases_match():
    assert any(
        "左甲状腺素与钙/铁/铝剂" in title
        for title in _titles("I take levothyroxine and calcium carbonate")
    )
    assert any(
        "喹诺酮类与矿物质" in title
        for title in _titles("levofloxacin and ferrous sulfate")
    )
    assert any(
        "四环素类与矿物质" in title
        for title in _titles("doxycycline with calcium")
    )


def test_rosuvastatin_is_not_cyp3a4_sensitive():
    assert not any(
        "他汀与西柚" in title
        for title in _titles("吃瑞舒伐他汀，能喝西柚汁吗")
    )
    assert any(
        "他汀与西柚" in title
        for title in _titles("吃辛伐他汀，能喝西柚汁吗")
    )
    assert any(
        "他汀与部分抗感染药" in title
        for title in _titles("吃辛伐他汀，同时吃红霉素")
    )


def test_egfr_stage_uses_unrounded_value():
    result = calculate_egfr(19, "male", 2.98)

    assert result["egfr"] == 30.0
    assert result["stage"] == "G4"


def test_api_accepts_micro_sign_for_egfr(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    client = TestClient(api_server.app)

    response = client.post("/api/tools/egfr", json={
        "age": 60,
        "sex": "male",
        "creatinine": 88.4,
        "unit": "μmol/L",
    })

    assert response.status_code == 200
    assert response.json()["creatinine_mg_dl"] == 1.0


def test_audit_c_accepts_chinese_sex_labels():
    result = calculate_audit_c(1, 1, 1, "女")

    assert result["risk"] == "偏高"
    assert "女性" in result["note"]
