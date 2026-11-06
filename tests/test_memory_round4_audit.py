"""第四轮记忆与持久化审计回归：墓碑、Redis 兜底、归档与备份轮转。"""
import json
import threading
from datetime import datetime, timedelta

from core.backup import _prune_backups
from memory.session_store import SessionStore
from memory.session_summary import SessionSummaryManager
from memory.short_term import ConversationHistory, ShortTermMemory


class FakeRedis:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.deleted = []

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value
        return True

    def delete(self, key):
        self.deleted.append(key)
        self.data.pop(key, None)
        return 1


def _redis_memory(redis_client):
    memory = object.__new__(ShortTermMemory)
    memory.storage_type = "redis"
    memory.redis_client = redis_client
    memory.sessions = {}
    memory._local_fallback_at = {}
    memory._cleared_sessions = set()
    memory._state_lock = threading.RLock()
    memory.entropy_manager = None
    memory._initialized = True
    return memory


def _history(session_id, contents):
    history = ConversationHistory(session_id=session_id)
    for role, content in contents:
        history.add_message(role, content)
    return history


def test_import_after_delete_revives_session(tmp_path):
    store = SessionStore(str(tmp_path / "import.db"))
    try:
        store.save_exchange("s1", "旧问题", "旧回答")
        assert store.delete_session("s1") is True
        assert store.save_exchange("s1", "迟到", "迟到") is None

        result = store.import_data({
            "sessions": [{
                "id": "s1",
                "title": "恢复的会诊",
                "messages": [
                    {
                        "role": "user",
                        "content": "旧问题",
                        "created_at": "2026-11-01T10:00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "旧回答",
                        "created_at": "2026-11-01T10:01:00",
                    },
                ],
            }],
        })
        assert result["imported"] == 1

        assert store.is_session_deleted("s1") is False
        assert store.save_exchange("s1", "新问题", "新回答") is not None
        assert len(store.get_messages("s1")) == 4
    finally:
        store.close()


def test_get_session_prefers_newer_local_fallback_over_stale_redis():
    old = _history("s", [("user", "OLD_Q"), ("assistant", "OLD_A")])
    redis = FakeRedis({"session:s": json.dumps(old.to_dict())})
    memory = _redis_memory(redis)

    local = _history("s", [
        ("user", "OLD_Q"), ("assistant", "OLD_A"), ("user", "NEW_Q"),
    ])
    memory.sessions["s"] = local
    memory._local_fallback_at["s"] = datetime.now()

    history = memory.get_session("s")

    assert history is local
    assert [m["content"] for m in history.messages][-1] == "NEW_Q"


def test_add_message_cannot_resurrect_session_cleared_concurrently():
    old = _history("s", [("user", "SECRET_Q"), ("assistant", "SECRET_A")])

    class BlockingRedis(FakeRedis):
        def __init__(self, data):
            super().__init__(data)
            self.started = threading.Event()
            self.allow = threading.Event()

        def get(self, key):
            self.started.set()
            self.allow.wait(3)
            return super().get(key)

    redis = BlockingRedis({"session:s": json.dumps(old.to_dict())})
    memory = _redis_memory(redis)

    def clearer():
        # 等 add_message 读到 Redis 旧值后再开始清理，复现清理与写回的竞态
        redis.started.wait(3)
        redis.allow.set()
        memory.clear_session("s")

    thread = threading.Thread(target=clearer)
    thread.start()
    try:
        memory.add_message("s", "user", "NEW_Q")
    finally:
        thread.join(5)

    assert "s" in memory._cleared_sessions
    assert "session:s" not in redis.data
    assert memory.get_session("s") is None


def test_archive_older_than_uses_exact_age(tmp_path):
    store = SessionStore(str(tmp_path / "archive.db"))
    try:
        now = datetime.now()
        store.save_exchange("recent", "问题", "回答")
        store.save_exchange("old", "问题", "回答")
        store._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = 'recent'",
            ((now - timedelta(hours=12)).isoformat(sep=" "),),
        )
        store._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = 'old'",
            ((now - timedelta(hours=25)).isoformat(sep=" "),),
        )
        store._conn.commit()

        archived = store.archive_older_than(1)

        def _archived(session_id):
            row = store._conn.execute(
                "SELECT archived FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return int(row["archived"])

        assert archived == 1
        assert _archived("recent") == 0
        assert _archived("old") == 1
    finally:
        store.close()


def test_prune_backups_protects_just_created_file(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    new_by_clock = backup_dir / "medix-backup-20260101-000000.json"
    old_valid = backup_dir / "medix-backup-20260102-000000.json"
    for path in (new_by_clock, old_valid):
        path.write_text("{}", encoding="utf-8")

    # 模拟时钟回拨：刚创建的文件名比旧备份更“老”
    _prune_backups(backup_dir, 2, protect=new_by_clock)

    assert new_by_clock.exists()
    assert old_valid.exists()


def test_summary_paths_are_injective_and_legacy_files_are_deleted(tmp_path):
    manager = SessionSummaryManager(str(tmp_path / "summaries"))

    colon = manager._get_summary_path("a:b", create=False)
    underscore = manager._get_summary_path("a_b", create=False)
    assert colon != underscore

    legacy = manager._get_summary_path("a:b", create=False, hashed=False)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("# 旧总结", encoding="utf-8")

    assert manager.delete_summary("a:b") is True
    assert not legacy.exists()


def test_month_usage_excludes_future_dated_messages(tmp_path):
    store = SessionStore(str(tmp_path / "usage.db"))
    try:
        store.save_exchange("s1", "本月问题", "本月回答")
        store.import_data({
            "sessions": [{
                "id": "future",
                "title": "未来会诊",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "未来回答",
                        "created_at": "2999-01-01T00:00:00",
                    },
                ],
            }],
        })

        usage = store.month_usage()

        assert usage["turns"] == 1
    finally:
        store.close()
