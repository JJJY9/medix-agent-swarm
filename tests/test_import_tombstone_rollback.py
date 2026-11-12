"""导入事务回滚后删除墓碑不能被提前清除。"""
import pytest

from core.backup import MAX_BACKUP_SESSION_MESSAGES
from memory.session_store import SessionStore


def test_import_rollback_keeps_tombstone(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_exchange("s1", "问题", "答复")
        assert store.delete_session("s1")
        assert store.is_session_deleted("s1")

        overflow = MAX_BACKUP_SESSION_MESSAGES + 1
        payload = {
            "sessions": [
                {
                    "id": "s1",
                    "messages": [
                        {"role": "user", "content": "q"},
                        {"role": "assistant", "content": "a"},
                    ],
                },
                {
                    "id": "s2",
                    "messages": [
                        {"role": "user", "content": "x"}
                        for _ in range(overflow)
                    ],
                },
            ],
        }

        with pytest.raises(ValueError):
            store.import_data(payload)

        # 事务回滚后 s1 未恢复，墓碑必须保留，
        # 否则在途会诊仍能把已删除的会话写回
        assert store.get_session("s1") is None
        assert store.is_session_deleted("s1")
    finally:
        store.close()
