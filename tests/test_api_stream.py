"""API SSE 事件流的离线测试（假 Coordinator，不访问模型）。"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import api_server


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
        }


@pytest.fixture(autouse=True)
def fresh_state():
    api_server.state.lock = asyncio.Lock()
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
