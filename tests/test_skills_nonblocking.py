"""技能异步/错误信息健壮性回归测试。"""
import asyncio
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import core.session_context as session_context

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeLongTermMemory:
    enabled = True

    def __init__(self):
        self.thread = None

    def search_similar_sessions(self, query, limit, user_id):
        self.thread = threading.current_thread().name
        return [{
            "content": "既往类似案例",
            "score": 0.9,
            "metadata": {},
        }]


def test_similar_cases_search_runs_off_event_loop(monkeypatch):
    module = _load(
        PROJECT_ROOT / ".claude" / "skills" / "search-similar-cases"
        / "script" / "search.py",
        "search_similar_cases_skill",
    )
    fake = FakeLongTermMemory()
    monkeypatch.setattr(
        session_context, "get_memory_scope", lambda: "scope"
    )
    monkeypatch.setitem(
        sys.modules, "memory.long_term",
        SimpleNamespace(LongTermMemory=lambda: fake),
    )

    result = asyncio.run(
        module.search_similar_cases("高血压", max_results=3)
    )

    assert fake.thread is not None
    assert fake.thread != "MainThread"
    assert "相似历史案例" in result["answer"]


def test_deep_research_error_does_not_echo_exception():
    text = (
        PROJECT_ROOT / ".claude" / "skills" / "deep-research"
        / "script" / "research.py"
    ).read_text(encoding="utf-8")

    assert "深度研究失败：{str(e)}" not in text
    assert "深度研究失败，请稍后重试" in text
