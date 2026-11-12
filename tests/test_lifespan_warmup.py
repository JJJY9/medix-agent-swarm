"""服务生命周期中知识库预热任务的回归测试。"""
from fastapi.testclient import TestClient

import api_server


def _run_lifespan(monkeypatch, skip, called):
    async def fake_warm_up():
        called.append(True)

    monkeypatch.setattr(api_server.state, "warm_up", fake_warm_up)
    if skip:
        monkeypatch.setenv("MEDIX_SKIP_WARMUP", "1")
    else:
        monkeypatch.delenv("MEDIX_SKIP_WARMUP", raising=False)
    original = api_server.state.coordinator
    api_server.state.coordinator = object()
    try:
        with TestClient(api_server.app):
            pass
    finally:
        api_server.state.coordinator = original


def test_warmup_is_skipped_for_tests(monkeypatch):
    called = []
    _run_lifespan(monkeypatch, skip=True, called=called)

    assert called == []
    assert api_server.state.warmup_task is None


def test_warmup_task_is_created_and_cancelled(monkeypatch):
    called = []
    _run_lifespan(monkeypatch, skip=False, called=called)

    assert called == [True]
    assert api_server.state.warmup_task is None


def test_later_coordinator_success_clears_startup_error(monkeypatch):
    import swarm

    class FakeCoordinator:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(swarm, "SwarmCoordinator", FakeCoordinator)
    state = api_server.AppState()
    state.startup_error = "temporary failure"

    assert state.get_coordinator() is not None
    assert state.startup_error is None
