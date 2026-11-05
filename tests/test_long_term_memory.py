"""长期记忆写入与检索的离线测试（使用假 Mem0 客户端）。"""
from memory.long_term import LongTermMemory


class FakeMem0:
    def __init__(self, add_result=None, search_result=None):
        self.add_result = add_result if add_result is not None else {"id": "mem-1"}
        self.search_result = search_result if search_result is not None else []
        self.added = None
        self.searched = None

    def add(self, **kwargs):
        self.added = kwargs
        return self.add_result

    def search(self, **kwargs):
        self.searched = kwargs
        return self.search_result


def _memory(client):
    memory = object.__new__(LongTermMemory)
    memory.enabled = True
    memory.mem0 = client
    memory.entropy_manager = None
    return memory


def test_add_session_summary_writes_newline():
    client = FakeMem0()
    memory = _memory(client)

    memory_id = memory.add_session_summary("s1", "头痛怎么办", "建议休息")

    assert memory_id == "mem-1"
    content = client.added["messages"][0]["content"]
    assert "\n回答" in content
    assert "\\n" not in content


def test_add_session_summary_handles_empty_results():
    client = FakeMem0(add_result={"results": []})
    memory = _memory(client)

    assert memory.add_session_summary("s1", "问题", "回答") is None


def test_add_session_summary_extracts_id_from_results():
    client = FakeMem0(add_result={"results": [{"id": "mem-9"}]})
    memory = _memory(client)

    assert memory.add_session_summary("s1", "问题", "回答") == "mem-9"


def test_search_similar_sessions_handles_empty_dict():
    client = FakeMem0(search_result={"results": []})
    memory = _memory(client)

    assert memory.search_similar_sessions("头痛") == []
