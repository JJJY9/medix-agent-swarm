"""第二轮审计的用药安全回归测试。"""
from core.medication_safety import check_medication_safety


def _titles(result):
    return [alert["title"] for alert in result["alerts"]]


def test_not_stopped_medication_still_counts_as_current_use():
    for question in (
        "我没有停用华法林，能吃阿司匹林吗？",
        "暂未停用华法林，能吃阿司匹林吗？",
        "不要停用华法林，能吃阿司匹林吗？",
    ):
        titles = _titles(check_medication_safety(question))
        assert "华法林与非甾体抗炎药" in titles, (question, titles)


def test_stopped_medication_is_not_current_use():
    for question in (
        "已经停用华法林两周，能吃阿司匹林吗？",
        "停用华法林了，能吃阿司匹林吗？",
        "不再服用华法林，能吃阿司匹林吗？",
    ):
        titles = _titles(check_medication_safety(question))
        assert "华法林与非甾体抗炎药" not in titles, (question, titles)


def test_warfarin_amiodarone_interaction():
    titles = _titles(check_medication_safety("华法林、胺碘酮"))
    assert "华法林与胺碘酮" in titles


def test_warfarin_fluoroquinolone_interaction():
    for question in ("华法林、左氧氟沙星", "华法林和环丙沙星"):
        titles = _titles(check_medication_safety(question))
        assert "华法林与氟喹诺酮类" in titles, (question, titles)
