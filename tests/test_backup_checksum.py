"""备份校验和与篡改检测的回归测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore, _backup_checksum


@pytest.fixture
def store(tmp_path):
    instance = SessionStore(str(tmp_path / "checksum.db"))
    yield instance
    instance.close()


def _export_with_session(store):
    store.save_exchange("s1", "问题", "答复")
    return store.export_data()


def test_export_contains_checksum_and_roundtrips(store, tmp_path):
    data = _export_with_session(store)

    assert data["checksum"].startswith("sha256:")

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        result = target.import_data(data)
        assert result["imported"] == 1
    finally:
        target.close()


def test_tampered_backup_is_rejected(store):
    data = _export_with_session(store)
    data["sessions"][0]["title"] = "被篡改的标题"

    with pytest.raises(ValueError, match="校验失败"):
        store.import_data(data)


def test_old_backup_without_new_fields_still_verifies(store):
    """升级后新增的校验字段缺失时，历史备份仍可导入。"""
    store.save_exchange("s1", "问题", "答复")
    data = store.export_data()
    full_checksum = data["checksum"]

    data.pop("vaccinations")
    data["checksum"] = _backup_checksum(data)
    assert data["checksum"] != full_checksum

    result = store.import_data(data)

    assert result["imported"] == 0
    assert result["skipped"] == 1


def test_backup_without_checksum_still_imports(store):
    payload = {
        "sessions": [{
            "id": "old-1",
            "title": "旧备份",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "messages": [
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧答复"},
            ],
        }],
    }

    result = store.import_data(payload)

    assert result["imported"] == 1


def test_api_rejects_tampered_backup(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "api-checksum.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        client = TestClient(api_server.app)
        store.save_exchange("s1", "问题", "答复")
        data = client.get("/api/sessions/export").json()
        assert data["checksum"].startswith("sha256:")

        data["sessions"][0]["title"] = "篡改"
        response = client.post("/api/sessions/import", json=data)

        assert response.status_code == 400
        assert "校验失败" in response.json()["detail"]

        # 原样导入仍然成功
        data = client.get("/api/sessions/export").json()
        ok = client.post("/api/sessions/import", json=data)
        assert ok.status_code == 200
    finally:
        store.close()


def test_api_imports_legacy_backup_missing_new_fields(tmp_path, monkeypatch):
    """API 导入也必须接受缺少后续新增字段的历史备份。"""
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "api-legacy.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        client = TestClient(api_server.app)
        store.save_exchange("s1", "问题", "答复")
        data = client.get("/api/sessions/export").json()
        data.pop("vaccinations")
        data["checksum"] = _backup_checksum(data)

        response = client.post("/api/sessions/import", json=data)

        assert response.status_code == 200, response.text
        assert response.json()["skipped"] == 1
    finally:
        store.close()


def _as_browser_json(value):
    """模拟浏览器 JSON.stringify：整数值的 float 会变成 int。"""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _as_browser_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_browser_json(item) for item in value]
    return value


def test_checksum_survives_browser_number_normalization(store, tmp_path):
    store.save_exchange("s1", "问题", "答复")
    store.add_metric("", "weight", 70)
    data = store.export_data()
    browser_data = _as_browser_json(data)

    target = SessionStore(str(tmp_path / "browser-target.db"))
    try:
        result = target.import_data(browser_data)

        assert result["imported"] == 1
        assert result["metrics"] == 1
    finally:
        target.close()
