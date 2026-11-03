"""系统自检 API 的离线测试。"""
from fastapi.testclient import TestClient

import api_server


def test_diagnose_endpoint():
    client = TestClient(api_server.app)

    response = client.get("/api/diagnose")

    assert response.status_code == 200
    report = response.json()
    assert report["python_ok"] is True
    assert "packages" in report
    assert "knowledge" in report
    assert "api_key" not in report["llm_config"]
