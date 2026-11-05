"""备份导入上限测试：与导出口径一致，超限显式报错而不是静默截断。"""
import pytest

import memory.session_store as store_module
from memory.session_store import IMPORT_LIST_LIMITS, SessionStore


@pytest.fixture
def store(tmp_path):
    instance = SessionStore(str(tmp_path / "sessions.db"))
    yield instance
    instance.close()


def test_import_rejects_too_many_profiles(store, monkeypatch):
    monkeypatch.setitem(IMPORT_LIST_LIMITS, "profiles", 2)
    payload = {
        "sessions": [],
        "profiles": [{"name": f"档案{i}"} for i in range(3)],
    }

    with pytest.raises(ValueError, match="2 条档案"):
        store.import_data(payload)


def test_import_rejects_too_many_reminders(store, monkeypatch):
    monkeypatch.setitem(IMPORT_LIST_LIMITS, "reminders", 2)
    payload = {
        "sessions": [],
        "reminders": [{"title": f"提醒{i}"} for i in range(3)],
    }

    with pytest.raises(ValueError, match="2 条提醒"):
        store.import_data(payload)


def test_import_limits_cover_export_volume():
    # 导出不限制提醒/档案等列表，导入上限必须足够大，
    # 否则应用自己导出的备份无法恢复
    assert IMPORT_LIST_LIMITS["reminders"] >= 100000
    assert IMPORT_LIST_LIMITS["profiles"] >= 100000
    assert IMPORT_LIST_LIMITS["health_metrics"] >= 100000


def test_import_accepts_large_reminder_backup(store):
    payload = {
        "sessions": [],
        "reminders": [
            {"title": f"提醒{i}", "due_at": "2026-11-06"}
            for i in range(2500)
        ],
    }

    result = store.import_data(payload)

    assert result["reminders"] == 2500
