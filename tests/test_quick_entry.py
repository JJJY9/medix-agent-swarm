"""一句话健康记录解析与 API 测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.quick_entry import QuickEntryError, parse_quick_metric
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


def test_parse_blood_pressure_with_context():
    parsed = parse_quick_metric("血压 150/95 早上")
    assert parsed["metric"] == "bp"
    assert parsed["value1"] == 150
    assert parsed["value2"] == 95
    assert parsed["context"] == "morning"
    assert parsed["context_label"] == "晨起"
    assert parsed["supports_context"] is True

    compact = parse_quick_metric("血压150/95晨起")
    assert compact["value1"] == 150
    assert compact["context"] == "morning"


def test_parse_chinese_numbers():
    parsed = parse_quick_metric("血压 一百五十/九十五 晚上")
    assert parsed["value1"] == 150
    assert parsed["value2"] == 95
    assert parsed["context"] == "evening"

    weight = parse_quick_metric("体重 六十二点五")
    assert weight["metric"] == "weight"
    assert weight["value1"] == 62.5

    # 省略低位单位的中文写法：一百二=120、一百五=150、两百五=250
    assert parse_quick_metric("血压 一百二/八十")["value1"] == 120
    assert parse_quick_metric("体重 一百五")["value1"] == 150
    assert parse_quick_metric("体重 两百五")["value1"] == 250


def test_numbers_must_be_next_to_metric():
    parsed = parse_quick_metric("餐后2小时血糖7.8")
    assert parsed["value1"] == 7.8
    assert parsed["context"] == "postprandial"

    assert parse_quick_metric("SpO2 98%")["value1"] == 98
    assert parse_quick_metric("早上8点体重65")["value1"] == 65
    assert parse_quick_metric("10月8日体重 62.5")["value1"] == 62.5
    assert parse_quick_metric("空腹8小时血糖 7.8")["value1"] == 7.8


def test_decimal_spellings():
    assert parse_quick_metric("血糖 7点8")["value1"] == 7.8
    assert parse_quick_metric("体温 37度2")["value1"] == 37.2
    assert parse_quick_metric("体温 三十七度二")["value1"] == 37.2


def test_time_and_date_prefixes_do_not_win():
    assert parse_quick_metric("早上8点血糖 7.8")["value1"] == 7.8
    assert parse_quick_metric("下午3点血糖 7.8")["value1"] == 7.8
    assert parse_quick_metric("空腹8小时血糖 7.8")["value1"] == 7.8
    assert parse_quick_metric("11/11 血压 150/95")["value1"] == 150
    # 日期对出现在指标名之后时，跳过非法的 11/11，采用后面的有效血压对
    assert parse_quick_metric("血压 11/11 150/95")["value1"] == 150


def test_units_are_converted_or_rejected():
    assert parse_quick_metric("体重 120斤")["value1"] == 60
    assert parse_quick_metric("血糖 126mg/dL")["value1"] == 7
    assert parse_quick_metric("体温 98.6°F")["value1"] == 37
    with pytest.raises(QuickEntryError) as error:
        parse_quick_metric("体重 120cm")
    assert error.value.code == "unknown_unit"


def test_signed_and_leading_dot_numbers_are_rejected_by_range():
    for text in ("血糖 .8", "血糖 -7.8"):
        with pytest.raises(QuickEntryError) as error:
            parse_quick_metric(text)
        assert error.value.code == "out_of_range", text


def test_parse_glucose_contexts():
    fasting = parse_quick_metric("血糖 7.8 空腹")
    assert fasting["metric"] == "glucose"
    assert fasting["value1"] == 7.8
    assert fasting["context"] == "fasting"
    assert fasting["context_label"] == "空腹"

    postprandial = parse_quick_metric("餐后血糖 11.2")
    assert postprandial["context"] == "postprandial"

    plain = parse_quick_metric("血糖 6.1")
    assert plain["context"] == ""
    assert plain["supports_context"] is True


def test_parse_other_metrics_and_unit_inference():
    assert parse_quick_metric("体温 38.2℃")["metric"] == "temperature"
    assert parse_quick_metric("38.5度")["metric"] == "temperature"
    assert parse_quick_metric("心率 88 次/分")["metric"] == "heart_rate"
    assert parse_quick_metric("血氧 97%")["metric"] == "spo2"
    assert parse_quick_metric("血氧饱和度 97%")["metric"] == "spo2"
    assert parse_quick_metric("腰围 85cm")["metric"] == "waist"
    assert parse_quick_metric("睡眠 7.5 小时")["metric"] == "sleep"
    assert parse_quick_metric("睡了 8 小时")["value1"] == 8
    assert parse_quick_metric("睡眠 7.5")["value1"] == 7.5
    assert parse_quick_metric("步数 8000")["metric"] == "steps"
    assert parse_quick_metric("步数 8000 步")["value1"] == 8000
    assert parse_quick_metric("6500步")["metric"] == "steps"
    assert parse_quick_metric("走了 6500")["value1"] == 6500
    assert parse_quick_metric("饮水 1500 ml")["metric"] == "water"
    assert parse_quick_metric("喝水 1200毫升")["value1"] == 1200
    assert parse_quick_metric("喝了 800ml")["value1"] == 800
    assert parse_quick_metric("1500ml")["metric"] == "water"
    assert parse_quick_metric("饮水 1.5 升")["value1"] == 1500
    assert parse_quick_metric("喝了 2L 水")["value1"] == 2000
    assert parse_quick_metric("饮水 1.5l")["value1"] == 1500
    assert parse_quick_metric("喝水 2 公升")["value1"] == 2000
    # 「升高」不是单位，不能影响血糖解析
    assert parse_quick_metric("血糖升高 7.8")["value1"] == 7.8
    assert parse_quick_metric("血糖 7.8 mmol/L")["value1"] == 7.8
    assert parse_quick_metric("体重 62.5 kg")["metric"] == "weight"
    assert parse_quick_metric("62.5kg")["metric"] == "weight"
    assert parse_quick_metric("血压 150/95 mmHg")["metric"] == "bp"
    assert parse_quick_metric("150/95 mmhg")["value1"] == 150
    assert parse_quick_metric("97%")["metric"] == "spo2"
    assert parse_quick_metric("88 次/分")["metric"] == "heart_rate"


def test_metric_range_boundaries():
    assert parse_quick_metric("血糖 1.0")["value1"] == 1.0
    assert parse_quick_metric("血糖 50.0")["value1"] == 50.0
    for text in ("血糖 0.9", "血糖 50.1"):
        with pytest.raises(QuickEntryError) as error:
            parse_quick_metric(text)
        assert error.value.code == "out_of_range", text
    assert parse_quick_metric("血压 50/30")["value1"] == 50
    assert parse_quick_metric("血压 300/200")["value1"] == 300


def test_unit_inference_rejects_other_measurements():
    # cm、mmol/L 等可能属于身高、胆固醇等其他项目，不能仅凭单位推断
    for text in (
        "身高 175cm", "总胆固醇 5.2 mmol/L", "呼吸 40 次/分", "85cm",
        "7.8mmol/l",
    ):
        with pytest.raises(QuickEntryError) as error:
            parse_quick_metric(text)
        assert error.value.code == "unknown_metric", text


def test_blood_pressure_validation_matches_save_rules():
    for text in ("血压 90/95", "血压 150/210", "血压 150/20"):
        with pytest.raises(QuickEntryError) as error:
            parse_quick_metric(text)
        assert error.value.code == "out_of_range", text
    assert parse_quick_metric("血压 90/60")["value1"] == 90


def test_bp_preview_acceptance_matches_store(client_and_store):
    _client, store = client_and_store
    parsed = parse_quick_metric("血压 150/45")
    assert parsed["value2"] == 45
    assert store.add_metric("", "bp", 150, 45) is not None
    for first, second, text in (
        (90, 150, "血压 90/150"),
        (150, 250, "血压 150/250"),
        (50, 50, "血压 50/50"),
    ):
        with pytest.raises(QuickEntryError) as error:
            parse_quick_metric(text)
        assert error.value.code == "out_of_range", text
        assert store.add_metric("", "bp", first, second) is None


def test_parse_rejects_ambiguous_or_invalid_input():
    cases = {
        "": "empty",
        "血压 150": "bp_needs_two",
        "血糖 7.8 心率 80": "multiple_metrics",
        "今天天气不错": "unknown_metric",
        "60hr": "unknown_metric",
        "血压 400/95": "out_of_range",
        "血糖": "missing_value",
    }
    for text, code in cases.items():
        with pytest.raises(QuickEntryError) as error:
            parse_quick_metric(text)
        assert error.value.code == code, text


def test_parse_ignores_context_not_allowed_for_metric():
    parsed = parse_quick_metric("血压 150/95 空腹")
    assert parsed["context"] == ""


def test_quick_parse_api_returns_preview(client_and_store):
    client, _store = client_and_store
    response = client.post(
        "/api/metrics/quick-parse", json={"text": "血压 150/95 早上"}
    )
    assert response.status_code == 200
    preview = response.json()["preview"]
    assert preview["metric"] == "bp"
    assert preview["value1"] == 150
    assert preview["context_label"] == "晨起"


def test_quick_parse_api_does_not_write_metrics(client_and_store):
    client, _store = client_and_store
    before = client.get("/api/metrics").json()["metrics"]
    client.post(
        "/api/metrics/quick-parse",
        json={"text": "血糖 7.8 空腹 体重 62.5"},
    )
    client.post(
        "/api/metrics/quick-parse", json={"text": "血糖 7.8 空腹"}
    )
    after = client.get("/api/metrics").json()["metrics"]
    assert len(after) == len(before)


def test_quick_parse_api_returns_error_code(client_and_store):
    client, _store = client_and_store
    response = client.post(
        "/api/metrics/quick-parse", json={"text": "血压 150"}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "bp_needs_two"
    assert "收缩压" in body["detail"]


def test_confirmed_preview_saves_through_existing_endpoint(client_and_store):
    client, _store = client_and_store
    preview = client.post(
        "/api/metrics/quick-parse", json={"text": "血糖 7.8 空腹"}
    ).json()["preview"]
    saved = client.post("/api/metrics", json={
        "profile": "妈妈",
        "metric": preview["metric"],
        "value1": preview["value1"],
        "value2": preview["value2"],
        "context": preview["context"],
        "note": preview["text"],
    })
    assert saved.status_code == 200
    metric = saved.json()["metric"]
    assert metric["metric"] == "glucose"
    assert metric["value1"] == 7.8
    assert metric["context"] == "fasting"
    assert metric["note"] == "血糖 7.8 空腹"
