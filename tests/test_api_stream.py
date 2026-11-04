"""API SSE 事件流的离线测试（假 Coordinator，不访问模型）。"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api_server
from core.llm_client import LLMError


class FakeCoordinator:
    worker_pool = []

    def __init__(self, answer="流式答案"):
        self.answer = answer

    async def process(self, question, context=None, session_id=None, on_token=None):
        if on_token:
            on_token("流式")
            on_token("答案")
        return {
            "answer": self.answer,
            "swarm_enabled": True,
            "session_id": session_id,
            "agents_involved": ["consultation_agent"],
            "subtasks_completed": 1,
            "subtasks_failed": 0,
            "total_time": 1.0,
            "timeout_occurred": False,
            "suggestions": [],
            "disclaimer": "仅供参考",
            "triage": {"level": "none", "matches": [], "message": ""},
            "agent_contributions": [{
                "agent_id": "consultation_agent",
                "subtask_id": "s1",
                "task_type": "consultation_agent_task",
                "content": "科室原始意见",
                "confidence": 1.0,
            }],
            "sources": [{
                "source": "临床指南数据库",
                "filename": "20.txt",
                "snippet": "指南片段",
                "score": 0.8,
            }],
        }


@pytest.fixture(autouse=True)
def fresh_state():
    api_server.state.lock = asyncio.Lock()
    api_server.chat_limiter.clear()
    yield


def _events(text):
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_stream_emits_tokens_and_result():
    api_server.state.coordinator = FakeCoordinator()
    client = TestClient(api_server.app)

    response = client.post(
        "/api/chat/stream",
        json={"question": "我头痛怎么办", "session_id": "api-stream-test"},
    )

    assert response.status_code == 200
    events = _events(response.text)
    types = [event["type"] for event in events]

    assert types[0] == "stage"
    assert "token" in types
    tokens = "".join(
        event["delta"] for event in events if event["type"] == "token"
    )
    result = next(event for event in events if event["type"] == "result")
    assert tokens == "流式答案"
    assert result["answer"] == "流式答案"
    assert result["agent_contributions"][0]["agent_name"] == "健康咨询"
    assert result["sources"][0]["filename"] == "20.txt"


class LiveProgressCoordinator(FakeCoordinator):
    """模拟 Worker 执行中，验证 SSE 会先推送 agent_started。"""

    def __init__(self):
        super().__init__()
        self.worker = SimpleNamespace(shared_context=None)
        self.worker_pool = [self.worker]

    async def process(self, question, context=None, session_id=None, on_token=None):
        self.worker.shared_context = SimpleNamespace(
            agent_contributions={},
            task_decomposition={
                "s1": SimpleNamespace(
                    assigned_agent="consultation_agent",
                    status=SimpleNamespace(value="in_progress"),
                )
            },
        )
        await asyncio.sleep(0.25)
        return await super().process(question, context, session_id, on_token)


def test_stream_emits_agent_started_before_result():
    api_server.state.coordinator = LiveProgressCoordinator()
    client = TestClient(api_server.app)

    response = client.post(
        "/api/chat/stream",
        json={"question": "我头痛怎么办", "session_id": "api-started-test"},
    )

    assert response.status_code == 200
    events = _events(response.text)
    types = [event["type"] for event in events]

    assert "agent_started" in types
    started = events[types.index("agent_started")]
    assert started["agent_id"] == "consultation_agent"
    assert started["name"] == "健康咨询"
    assert types.index("agent_started") < types.index("result")


def test_stream_emits_context_aware_triage():
    api_server.state.coordinator = FakeCoordinator()
    client = TestClient(api_server.app)

    response = client.post(
        "/api/chat/stream",
        json={
            "question": "宝宝发烧到 38 度",
            "session_id": "api-infant-fever-test",
            "context": {"age": "2个月"},
        },
    )

    assert response.status_code == 200
    events = _events(response.text)
    triage = next(event for event in events if event["type"] == "triage")

    assert triage["level"] == "emergency"
    assert "3 个月以下婴儿发热" in triage["matches"]


def test_stream_emits_medication_safety_before_result():
    api_server.state.coordinator = FakeCoordinator()
    client = TestClient(api_server.app)

    response = client.post(
        "/api/chat/stream",
        json={
            "question": "我在吃华法林，头痛能吃布洛芬吗？",
            "session_id": "api-medication-test",
        },
    )

    assert response.status_code == 200
    events = _events(response.text)
    types = [event["type"] for event in events]

    assert "medication_safety" in types
    safety = events[types.index("medication_safety")]
    assert safety["level"] == "warning"
    assert any("华法林" in alert["title"] for alert in safety["alerts"])
    assert types.index("medication_safety") < types.index("result")


def test_chat_rate_limited(monkeypatch):
    api_server.state.coordinator = FakeCoordinator()
    monkeypatch.setenv("MEDIX_RATE_LIMIT", "2")
    client = TestClient(api_server.app)

    first = client.post("/api/chat", json={"question": "第一次", "session_id": "rate-1"})
    second = client.post("/api/chat", json={"question": "第二次", "session_id": "rate-2"})
    third = client.post("/api/chat", json={"question": "第三次", "session_id": "rate-3"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    body = third.json()
    assert body["code"] == "rate_limited"
    assert "频繁" in body["detail"]
    assert "Retry-After" in third.headers

    # 非会诊接口不受影响
    api_server.state.coordinator = None
    assert client.get("/api/status").status_code == 200


def test_chat_with_non_finite_context_does_not_500():
    api_server.state.coordinator = FakeCoordinator()
    client = TestClient(api_server.app)

    response = client.post(
        "/api/chat",
        content='{"question":"你好","context":{"age":1e999}}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200


def test_stream_emits_triage_before_result():
    api_server.state.coordinator = FakeCoordinator()
    client = TestClient(api_server.app)

    response = client.post(
        "/api/chat/stream",
        json={"question": "我突然胸痛、呼吸困难", "session_id": "api-triage-test"},
    )

    assert response.status_code == 200
    events = _events(response.text)
    types = [event["type"] for event in events]

    assert "triage" in types
    assert types.index("triage") < types.index("result")

    triage = events[types.index("triage")]
    assert triage["level"] == "emergency"
    assert "胸痛" in triage["matches"]
    assert "呼吸困难" in triage["matches"]


class SwarmAwareCoordinator(FakeCoordinator):
    """记录请求是否允许 Swarm。"""

    def __init__(self):
        super().__init__()
        self.received = None

    async def process(
        self, question, context=None, session_id=None, on_token=None,
        enable_swarm=True
    ):
        self.received = enable_swarm
        return await super().process(question, context, session_id, on_token)


def test_chat_forwards_single_mode():
    coordinator = SwarmAwareCoordinator()
    api_server.state.coordinator = coordinator
    client = TestClient(api_server.app)

    response = client.post(
        "/api/chat",
        json={
            "question": "复杂问题",
            "session_id": "single-mode-test",
            "enable_swarm": False,
        },
    )

    assert response.status_code == 200
    assert coordinator.received is False


def test_stream_forwards_single_mode():
    coordinator = SwarmAwareCoordinator()
    api_server.state.coordinator = coordinator
    client = TestClient(api_server.app)

    response = client.post(
        "/api/chat/stream",
        json={
            "question": "复杂问题",
            "session_id": "single-stream-test",
            "enable_swarm": False,
        },
    )

    assert response.status_code == 200
    assert coordinator.received is False


class FailingCoordinator:
    worker_pool = []

    async def process(self, question, context=None, session_id=None, on_token=None):
        raise LLMError("llm_balance", "模型账户余额不足，请充值。")


def test_stream_emits_structured_llm_error():
    api_server.state.coordinator = FailingCoordinator()
    client = TestClient(api_server.app)

    response = client.post(
        "/api/chat/stream",
        json={"question": "你好", "session_id": "api-error-test"},
    )

    assert response.status_code == 200
    events = _events(response.text)
    error = next(event for event in events if event["type"] == "error")
    assert error["code"] == "llm_balance"
    assert "余额不足" in error["message"]


def test_chat_returns_structured_llm_error():
    api_server.state.coordinator = FailingCoordinator()
    client = TestClient(api_server.app)

    response = client.post("/api/chat", json={"question": "你好"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "llm_balance"
    assert "余额不足" in detail["message"]


def test_stream_saves_patient_profile(tmp_path, monkeypatch):
    import memory
    from memory.session_store import SessionStore

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange("profile-stream-test", "我头痛怎么办", "先前的答复")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    api_server.state.coordinator = FakeCoordinator()
    client = TestClient(api_server.app)

    try:
        first = client.post(
            "/api/chat/stream",
            json={
                "question": "我头痛怎么办",
                "session_id": "profile-stream-test",
                "patient_profile": "母亲",
            },
        )
        second = client.post(
            "/api/chat/stream",
            json={
                "question": "还是头痛",
                "session_id": "profile-stream-test",
                "patient_profile": "孩子",
            },
        )
        saved = store.get_session("profile-stream-test")
    finally:
        store.close()

    assert first.status_code == 200
    assert second.status_code == 200
    assert saved is not None
    assert saved["profile"] == "母亲"


def test_stream_does_not_create_session_for_profile(tmp_path, monkeypatch):
    import memory
    from memory.session_store import SessionStore

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    api_server.state.coordinator = FakeCoordinator()
    client = TestClient(api_server.app)

    try:
        response = client.post(
            "/api/chat/stream",
            json={
                "question": "我头痛怎么办",
                "session_id": "profile-phantom-test",
                "patient_profile": "母亲",
            },
        )
        saved = store.get_session("profile-phantom-test")
    finally:
        store.close()

    assert response.status_code == 200
    assert saved is None
