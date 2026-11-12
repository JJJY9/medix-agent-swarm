"""长期记忆删除的完整性与命名空间清理回归测试。"""
from memory.long_term import LongTermMemory


class PagingMem0:
    """模拟按 page/page_size 分页、删除后列表前移的 Mem0 服务。"""

    def __init__(self, count=250):
        self.memories = [
            {
                "id": f"m{index}",
                "metadata": {
                    "session_id": (
                        "target" if index % 3 == 0 else "other"
                    )
                },
            }
            for index in range(count)
        ]
        self.deleted = []

    def get_all(self, user_id=None, page=1, page_size=100):
        start = (page - 1) * page_size
        return {"results": self.memories[start:start + page_size]}

    def delete(self, memory_id):
        self.deleted.append(memory_id)
        self.memories = [
            item for item in self.memories
            if str(item["id"]) != str(memory_id)
        ]


def _memory_with(fake):
    memory = object.__new__(LongTermMemory)
    memory.enabled = True
    memory.mem0 = fake
    return memory


def test_delete_session_summary_removes_all_pages(monkeypatch):
    fake = PagingMem0()
    memory = _memory_with(fake)
    monkeypatch.setattr(
        "memory.long_term.session_memory_scope",
        lambda session_id: "target",
    )
    expected = [
        item["id"] for item in fake.memories
        if item["metadata"]["session_id"] == "target"
    ]

    deleted = memory.delete_session_summary("sess-1")

    assert deleted == len(expected)
    assert all(
        str(item["id"]) not in expected for item in fake.memories
    )


class DeleteAllFailsMem0:
    def __init__(self):
        self.memories = [
            {"id": "a", "metadata": {"x": 1}},
            {"id": "b", "metadata": "not-a-dict"},
            {"id": "c"},
        ]
        self.deleted = []

    def delete_all(self, user_id=None):
        raise RuntimeError("delete_all unsupported")

    def get_all(self, user_id=None, page=1, page_size=100):
        if page > 1:
            return {"results": []}
        return {"results": self.memories}

    def delete(self, memory_id):
        self.deleted.append(memory_id)


def test_delete_scope_fallback_removes_items_without_metadata():
    fake = DeleteAllFailsMem0()
    memory = _memory_with(fake)

    deleted = memory.delete_scope("ns")

    assert deleted == 3
    assert set(fake.deleted) == {"a", "b", "c"}


class StaticPageMem0:
    """服务端忽略 page 参数：每页都返回同一批，不能无限循环。"""

    def __init__(self):
        self.calls = 0
        self.deleted = []

    def get_all(self, user_id=None, page=1, page_size=100):
        self.calls += 1
        return {"results": [
            {"id": f"m{index}", "metadata": {"session_id": "target"}}
            for index in range(100)
        ]}

    def delete(self, memory_id):
        self.deleted.append(memory_id)


def test_pagination_not_advancing_stops_without_duplicates(monkeypatch):
    fake = StaticPageMem0()
    memory = _memory_with(fake)
    monkeypatch.setattr(
        "memory.long_term.session_memory_scope",
        lambda session_id: "target",
    )

    deleted = memory.delete_session_summary("sess-1")

    assert deleted == 100
    assert fake.calls <= 2
