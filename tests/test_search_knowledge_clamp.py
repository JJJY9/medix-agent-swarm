"""search_knowledge 返回条数上限的回归测试。"""
import asyncio
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    PROJECT_ROOT / ".claude" / "skills" / "search-knowledge"
    / "script" / "search.py"
)


def _load_skill():
    spec = importlib.util.spec_from_file_location(
        "search_knowledge_skill", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeKB:
    def __init__(self):
        self.top_k = None

    def search(self, query, top_k, filter_type=None):
        self.top_k = top_k
        return []


def test_max_results_is_clamped():
    module = _load_skill()
    fake = FakeKB()
    module._kb_instance = fake

    asyncio.run(module.search_knowledge("测试", max_results=-5))
    assert fake.top_k == 1

    asyncio.run(module.search_knowledge("测试", max_results=10000))
    assert fake.top_k == 20

    asyncio.run(module.search_knowledge("测试", max_results="bad"))
    assert fake.top_k == 5
