"""备份导入超限时的显式报错测试（不再静默截断）。"""
import pytest

from memory.session_store import SessionStore


@pytest.fixture
def store(tmp_path):
    instance = SessionStore(str(tmp_path / "sessions.db"))
    yield instance
    instance.close()


def test_import_rejects_too_many_profiles(store):
    payload = {
        "sessions": [],
        "profiles": [{"name": f"档案{i}"} for i in range(501)],
    }

    with pytest.raises(ValueError, match="500 条档案"):
        store.import_data(payload)


def test_import_rejects_too_many_reminders(store):
    payload = {
        "sessions": [],
        "reminders": [{"title": f"提醒{i}"} for i in range(2001)],
    }

    with pytest.raises(ValueError, match="2000 条提醒"):
        store.import_data(payload)


def test_import_within_limits_still_works(store):
    payload = {
        "sessions": [],
        "profiles": [{"name": "甲", "context": {"age": 40}}],
        "reminders": [{"title": "晨起测血压", "due_at": "2026-11-06"}],
    }

    result = store.import_data(payload)

    assert result["profiles"] == 1
    assert result["reminders"] == 1
