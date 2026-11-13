"""急症红旗预检的单元测试。"""
from core.triage import detect_red_flags


def test_common_symptoms_are_not_flagged():
    result = detect_red_flags("我最近有点咳嗽和流鼻涕，没有发烧")

    assert result["level"] == "none"
    assert result["matches"] == []
    assert result["message"] == ""


def test_chest_pain_and_dyspnea_are_flagged():
    result = detect_red_flags("我突然胸痛、呼吸困难，出冷汗")

    assert result["level"] == "emergency"
    assert result["matches"] == ["胸痛", "呼吸困难"]
    assert "120" in result["message"]


def test_negated_symptom_is_ignored():
    result = detect_red_flags("我没有胸痛，只是有点咳嗽")

    assert result["level"] == "none"


def test_mixed_negation_still_detects_other_symptoms():
    result = detect_red_flags("没有胸痛，但呼吸困难越来越重")

    assert result["level"] == "emergency"
    assert result["matches"] == ["呼吸困难"]


def test_stroke_like_signs_are_flagged():
    result = detect_red_flags("家里人突然说话不清、嘴歪、一侧肢体无力")

    assert result["level"] == "emergency"
    assert "言语不清" in result["matches"]
    assert "口角歪斜" in result["matches"]
    assert "单侧肢体无力" in result["matches"]


def test_meningitis_signs_are_flagged():
    result = detect_red_flags("发热三天，伴颈部僵硬和剧烈头痛")

    assert result["level"] == "emergency"
    assert "颈项强直" in result["matches"]
    assert "剧烈头痛" in result["matches"]


def test_duplicate_labels_are_collapsed():
    result = detect_red_flags("胸痛，心前区痛，胸口压榨感")

    assert result["matches"].count("胸痛") == 1
    assert "胸部压榨感" in result["matches"]


def test_empty_text_is_not_flagged():
    assert detect_red_flags("")["level"] == "none"
    assert detect_red_flags("   ")["level"] == "none"


def test_infant_fever_is_flagged_with_age_context():
    result = detect_red_flags("宝宝发烧到 38 度", {"age": "2个月"})

    assert result["level"] == "emergency"
    assert "3 个月以下婴儿发热" in result["matches"]
    assert "儿科" in result["message"]


def test_older_infant_fever_is_not_flagged_by_context_rule():
    result = detect_red_flags("宝宝发烧到 38 度", {"age": "6个月"})

    assert result["level"] == "none"


def test_negated_fever_is_not_flagged_by_context_rule():
    result = detect_red_flags("宝宝没有发烧，只是流鼻涕", {"age": "2个月"})

    assert result["level"] == "none"


def test_age_in_years_and_days_are_parsed():
    assert detect_red_flags("新生儿发烧", {"age": "新生儿"})["level"] == "emergency"
    assert detect_red_flags("发烧 38 度", {"age": "45天"})["level"] == "emergency"
    assert detect_red_flags("发烧 38 度", {"age": "58"})["level"] == "none"


def test_immunosuppressed_patient_with_fever_is_flagged():
    result = detect_red_flags("最近发热三天", {"history": "化疗后"})

    assert result["level"] == "emergency"
    assert "免疫功能低下伴发热" in result["matches"]
    assert "主诊医生" in result["message"]


def test_immunosuppressed_without_fever_is_not_flagged():
    result = detect_red_flags("最近有点乏力", {"history": "化疗后"})

    assert result["level"] == "none"


def test_anticoagulant_with_head_trauma_is_flagged():
    result = detect_red_flags(
        "昨晚摔倒撞到头部，现在有点头晕",
        {"medications": "华法林"},
    )

    assert result["level"] == "emergency"
    assert "抗凝治疗期间头部外伤" in result["matches"]
    assert "急诊" in result["message"]


def test_anticoagulant_without_head_trauma_is_not_flagged():
    result = detect_red_flags("今天有点头晕", {"medications": "华法林"})

    assert result["level"] == "none"


def test_context_rules_combine_with_keyword_matches():
    result = detect_red_flags(
        "宝宝发烧，刚才还抽搐了一次",
        {"age": "2个月"},
    )

    assert result["level"] == "emergency"
    assert "抽搐" in result["matches"]
    assert "3 个月以下婴儿发热" in result["matches"]


def test_urine_output_and_diabetes_emergencies_are_flagged():
    assert detect_red_flags("两天没有小便，人都肿了")["matches"] == ["无尿/少尿"]
    assert detect_red_flags("最近尿量明显减少")["matches"] == ["无尿/少尿"]
    assert detect_red_flags("医生说可能是糖尿病酮症酸中毒")["matches"] == ["糖尿病急症"]
    assert "糖尿病急症" in detect_red_flags("出现高渗性昏迷")["matches"]


def test_pregnancy_bleeding_is_flagged():
    result = detect_red_flags("怀孕两个月，突然阴道大量出血")

    assert result["level"] == "emergency"
    assert "孕产期出血" in result["matches"]
    assert "大量阴道出血" in result["matches"]


def test_new_red_flags_respect_negation():
    assert detect_red_flags("没有少尿，尿量正常")["level"] == "none"
    assert detect_red_flags("没有酮症酸中毒")["level"] == "none"


def test_uncertain_double_negation_still_flags():
    # 「尚未完全排除/暂时不除外」等表示仍有可能，不能当成否定漏报
    for text in (
        "尚未完全排除酮症酸中毒",
        "暂时不除外糖尿病酮症",
        "不能彻底排除酮症酸中毒",
        "未能排除酮症酸中毒",
        "未能明确地排除酮症酸中毒",
        "不可排除酮症酸中毒",
        "难以排除酮症酸中毒",
        # 「排除」后接系词时也不能漏判双重否定
        "未能排除是酮症酸中毒",
        "尚未完全排除为糖尿病酮症",
        # 「不排除出现/有/发生/伴有 X」是常见临床表述
        "医生说不排除出现酮症酸中毒的可能",
        "不排除有酮症酸中毒的可能",
        "不排除发生糖尿病酮症",
        "不能排除伴有酮症酸中毒",
    ):
        assert "糖尿病急症" in detect_red_flags(text)["matches"], text

    # 单纯「排除」及其完成态仍是阴性描述
    assert detect_red_flags("已排除酮症酸中毒")["level"] == "none"
    assert detect_red_flags("医生排除了酮症酸中毒")["level"] == "none"


def test_double_negation_with_fillers_still_flags():
    from core.triage import has_positive_mention

    for text in (
        "不排除有明显胸痛，需要做什么检查",
        "不能排除有过晕厥",
        "不排除曾经有抽搐",
        "不排除出现过晕厥",
        "尚未完全排除有明显胸痛",
        "不排除有任何胸痛",
        "不能排除有明确的胸痛",
    ):
        assert detect_red_flags(text)["level"] == "emergency", text

    assert has_positive_mention("不排除有明显胸痛", "胸痛") is True
    # 真实否定不能被重新误报
    assert detect_red_flags("没有明显胸痛")["level"] == "none"
    assert detect_red_flags("医生排除了明显胸痛")["level"] == "none"


def test_120_discouraging_phrases_are_not_advice():
    from core.triage import has_visit_recommendation

    assert has_visit_recommendation(
        "目前还没到需要拨打120的程度，先观察一下再说。"
    ) is False
    assert has_visit_recommendation(
        "还没到需要叫救护车的地步，先在家休息。"
    ) is False
    assert has_visit_recommendation("没敢拨打120") is False
    assert has_visit_recommendation("没顾上拨打120") is False


def test_visit_recommendation_negation_and_distance():
    from core.triage import has_visit_recommendation

    assert has_visit_recommendation("请立即去医院") is True
    assert has_visit_recommendation("目前还没有去医院") is False
    assert has_visit_recommendation("还没有去医院") is False
    assert has_visit_recommendation("尚未去医院就诊") is False
    assert has_visit_recommendation("无法去医院就诊") is False
    assert has_visit_recommendation("没有及时去医院") is False
    assert has_visit_recommendation("没有及时拨打120") is False
    assert has_visit_recommendation("疼得没法去医院") is False
    assert has_visit_recommendation("没人送医院") is False
    assert has_visit_recommendation("没送到医院") is False
    assert has_visit_recommendation("来不及去医院") is False
    assert has_visit_recommendation("没车送医院") is False
    assert has_visit_recommendation("没能送到医院") is False
    # 自然语序的否定描述
    assert has_visit_recommendation("没人送我去医院") is False
    assert has_visit_recommendation("没车送我去医院") is False
    assert has_visit_recommendation("没时间去医院") is False
    assert has_visit_recommendation("没能及时就医") is False
    assert has_visit_recommendation("未到需要去医院的程度") is False
    assert has_visit_recommendation("没严重到必须去医院") is False
    assert has_visit_recommendation("建议尽快去医院") is True
    # 否定词离得远时不能把真实建议一并否定
    assert has_visit_recommendation("没有明显诱因也应去医院检查") is True
    assert has_visit_recommendation("无明显诱因也应去医院") is True
    # 「无缓解/未来/无尿」中的字不能被当成对建议的否定
    assert has_visit_recommendation("胸痛无缓解请立即就医") is True
    assert has_visit_recommendation("若胸痛无缓解请立即就医") is True
    assert has_visit_recommendation("未来几天建议去医院复查") is True
    assert has_visit_recommendation("无尿请立即就医") is True
    assert has_visit_recommendation("胸痛无缓解请拨打120") is True


def test_negative_history_does_not_count_as_active():
    fever = "发热 38.5 度，咳嗽"
    assert detect_red_flags(fever, {"history": "无化疗史"})["level"] == "none"
    assert detect_red_flags(
        fever, {"history": "HIV抗体阴性"}
    )["level"] == "none"
    assert detect_red_flags(
        fever, {"history": "正在化疗"}
    )["matches"] == ["免疫功能低下伴发热"]
    # 长否定窗口不能把真实病史误判为否定
    assert detect_red_flags(
        fever, {"history": "无诱因化疗后"}
    )["matches"] == ["免疫功能低下伴发热"]
    assert detect_red_flags(
        fever, {"history": "化疗后复查阴性"}
    )["matches"] == ["免疫功能低下伴发热"]
    # 分句边界不能把上一段的「无」带到下一段真实病史上
    assert detect_red_flags(
        fever, {"history": "无；化疗后"}
    )["matches"] == ["免疫功能低下伴发热"]
    assert detect_red_flags(
        fever, {"history": "吸烟史：无，饮酒史：无，化疗中"}
    )["matches"] == ["免疫功能低下伴发热"]
    assert detect_red_flags(
        "摔倒后头部撞到门框", {"history": "饮酒史：无，华法林 3mg"}
    )["matches"] == ["抗凝治疗期间头部外伤"]

    trauma = "摔倒后头部撞到门框"
    assert detect_red_flags(
        trauma, {"history": "无抗凝药物"}
    )["level"] == "none"
    assert detect_red_flags(
        trauma, {"medications": "没有抗凝药物"}
    )["level"] == "none"


def test_active_medications_trigger_anticoagulant_head_injury():
    result = detect_red_flags(
        "今天摔倒撞到头了", {"active_medications": "华法林 3 mg 每日一次"}
    )

    assert "抗凝治疗期间头部外伤" in result["matches"]
