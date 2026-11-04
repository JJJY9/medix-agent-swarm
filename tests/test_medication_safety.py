"""用药安全规则引擎的离线测试。"""
import pytest

from core.medication_safety import check_medication_safety
from swarm.swarm_coordinator import SwarmCoordinator


def titles(result):
    return [alert["title"] for alert in result["alerts"]]


def test_plain_question_has_no_alert():
    result = check_medication_safety("最近头痛，需要注意什么？", None)

    assert result == {"level": "none", "alerts": []}


def test_penicillin_allergy_warns_amoxicillin():
    result = check_medication_safety(
        "感冒了能吃阿莫西林吗？",
        {"allergies": "青霉素"},
    )

    assert result["level"] == "warning"
    assert any("青霉素类过敏史" in title for title in titles(result))
    assert "阿莫西林" in result["alerts"][0]["detail"]


def test_allergy_without_matching_drug_does_not_alert():
    result = check_medication_safety("最近头痛", {"allergies": ["青霉素"]})

    assert result["alerts"] == []


def test_penicillin_allergy_flags_cephalosporin_cross_reaction():
    result = check_medication_safety(
        "可以吃头孢吗？",
        {"allergies": ["青霉素"]},
    )

    assert any("头孢类抗菌药" in alert["detail"] for alert in result["alerts"])


def test_warfarin_and_nsaid_interaction():
    result = check_medication_safety("华法林和阿司匹林能一起吃吗？")

    assert "华法林与非甾体抗炎药" in titles(result)
    assert result["level"] == "warning"


def test_duplicate_nsaids_are_flagged():
    result = check_medication_safety("我在吃布洛芬，还能吃双氯芬酸吗？")

    assert "重复使用非甾体抗炎药" in titles(result)
    assert "布洛芬" in result["alerts"][0]["detail"]


def test_acei_with_potassium_sparing_diuretic():
    result = check_medication_safety("缬沙坦和螺内酯能同服吗？")

    assert "降压药与保钾利尿剂" in titles(result)


def test_nitrate_with_pde5_inhibitor():
    result = check_medication_safety("吃硝酸甘油还能吃西地那非吗？")

    assert "硝酸酯与 PDE5 抑制剂" in titles(result)


def test_metformin_with_contrast_media():
    result = check_medication_safety("做增强CT前二甲双胍要停吗？")

    assert "二甲双胍与含碘造影剂" in titles(result)


def test_cephalosporin_and_alcohol_is_info_level():
    result = check_medication_safety("吃了头孢，今晚能喝酒吗？")

    assert result["level"] == "info"
    assert any(alert["severity"] == "info" for alert in result["alerts"])


def test_acetaminophen_and_alcohol_warns_liver_risk():
    result = check_medication_safety("吃了泰诺，晚上喝了酒，有事吗？")

    assert "对乙酰氨基酚与酒精" in titles(result)
    assert result["level"] == "warning"


def test_context_medications_participate_in_interactions():
    result = check_medication_safety(
        "最近有点头晕",
        {"medications": "华法林；布洛芬"},
    )

    assert "华法林与非甾体抗炎药" in titles(result)


def test_list_and_dict_context_are_flattened():
    result = check_medication_safety(
        "头晕",
        {"medications": ["华法林", {"name": "布洛芬"}]},
    )

    assert "华法林与非甾体抗炎药" in titles(result)


def test_alert_cap_keeps_allergy_first():
    result = check_medication_safety(
        "华法林、阿司匹林、布洛芬、二甲双胍、造影剂、硝酸甘油、西地那非、"
        "地高辛、胺碘酮、泼尼松，这些能一起吃吗？",
        {"allergies": "青霉素", "medications": "阿莫西林"},
    )

    assert len(result["alerts"]) <= 5
    assert any("青霉素类过敏史" in title for title in titles(result))


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("阿托伐他汀和克拉霉素能一起吃吗？", "他汀与部分抗感染药"),
        ("吃辛伐他汀能喝西柚汁吗？", "他汀与西柚"),
        ("优甲乐和碳酸钙能一起吗？", "左甲状腺素与钙/铁/铝剂"),
        ("左氧氟沙星和铁剂要间隔吗？", "喹诺酮类与矿物质"),
        ("多西环素和钙片能同服吗？", "四环素类与矿物质"),
        ("舍曲林和布洛芬一起吃会出血吗？", "SSRI 与非甾体抗炎药"),
        ("甲氨蝶呤和布洛芬能合用吗？", "甲氨蝶呤与非甾体抗炎药"),
        ("甲氨蝶呤和复方磺胺甲噁唑能一起吗？", "甲氨蝶呤与磺胺类"),
        ("别嘌醇和硫唑嘌呤能一起用吗？", "别嘌醇与硫唑嘌呤"),
        ("吗啡和阿普唑仑一起会呼吸抑制吗？", "阿片类与苯二氮䓬类"),
        ("格列美脲期间能喝酒吗？", "磺脲类降糖药与酒精"),
        ("打胰岛素能喝酒吗？", "胰岛素与酒精"),
        ("缬沙坦和布洛芬能同服吗？", "降压药与非甾体抗炎药"),
        ("碳酸锂和布洛芬能一起吗？", "锂盐与非甾体抗炎药"),
        ("碳酸锂和依那普利能一起用吗？", "锂盐与 ACEI/ARB"),
    ],
)
def test_additional_interaction_rules(question, expected):
    result = check_medication_safety(question)

    assert expected in titles(result)
    assert result["level"] == "warning"


def test_new_rules_do_not_fire_without_both_drugs():
    assert check_medication_safety("阿托伐他汀怎么吃？")["alerts"] == []
    assert check_medication_safety("优甲乐早上吃还是晚上吃？")["alerts"] == []
    assert check_medication_safety("左氧氟沙星怎么服用？")["alerts"] == []


def test_history_metadata_includes_medication_safety():
    safety = {
        "level": "warning",
        "alerts": [{"severity": "warning", "title": "示例", "detail": "示例"}],
    }

    metadata = SwarmCoordinator._history_metadata({
        "medication_safety": safety,
        "triage": {"level": "none", "matches": [], "message": ""},
    })

    assert metadata["medication_safety"] == safety


def test_active_medications_context_is_checked():
    result = check_medication_safety(
        "最近头晕", {"active_medications": "华法林；布洛芬"}
    )

    assert result["level"] == "warning"
    assert "华法林与非甾体抗炎药" in titles(result)
