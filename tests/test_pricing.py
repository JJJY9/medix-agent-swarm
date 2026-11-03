"""模型费用估算配置的测试。"""
from fastapi.testclient import TestClient

import api_server
from core.pricing import get_pricing


def test_pricing_disabled_without_config(monkeypatch):
    for name in ("MEDIX_PRICE_INPUT", "MEDIX_PRICE_OUTPUT", "MEDIX_CURRENCY"):
        monkeypatch.delenv(name, raising=False)

    assert get_pricing() is None


def test_pricing_reads_values_and_currency(monkeypatch):
    monkeypatch.setenv("MEDIX_PRICE_INPUT", "2")
    monkeypatch.setenv("MEDIX_PRICE_OUTPUT", "8")
    monkeypatch.setenv("MEDIX_CURRENCY", "$")

    assert get_pricing() == {
        "input_per_million": 2.0,
        "output_per_million": 8.0,
        "currency": "$",
    }


def test_pricing_allows_one_sided_price(monkeypatch):
    monkeypatch.setenv("MEDIX_PRICE_INPUT", "0")
    monkeypatch.setenv("MEDIX_PRICE_OUTPUT", "8")
    monkeypatch.delenv("MEDIX_CURRENCY", raising=False)

    pricing = get_pricing()

    assert pricing["input_per_million"] == 0
    assert pricing["output_per_million"] == 8
    assert pricing["currency"] == "¥"


def test_pricing_ignores_invalid_or_zero(monkeypatch):
    monkeypatch.setenv("MEDIX_PRICE_INPUT", "bad")
    monkeypatch.setenv("MEDIX_PRICE_OUTPUT", "0")

    assert get_pricing() is None


def test_status_exposes_pricing(monkeypatch):
    monkeypatch.setenv("MEDIX_PRICE_INPUT", "2")
    monkeypatch.setenv("MEDIX_PRICE_OUTPUT", "8")
    monkeypatch.setattr(api_server.state, "coordinator", None)
    client = TestClient(api_server.app)

    data = client.get("/api/status").json()

    assert data["pricing"]["input_per_million"] == 2.0
    assert data["pricing"]["output_per_million"] == 8.0
