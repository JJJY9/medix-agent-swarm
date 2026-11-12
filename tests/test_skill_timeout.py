"""Skill 执行超时兜底。"""
import asyncio
import time

import pytest

from core.skill_registry import (
    DEFAULT_SKILL_TIMEOUT,
    MAX_SKILL_TIMEOUT,
    SkillParameter,
    SkillRegistry,
    get_skill_timeout,
)


def _registry_with(function, name="slow"):
    registry = SkillRegistry()
    registry.register(
        name,
        function,
        "测试用 Skill",
        [SkillParameter("text", "string", "输入", required=True)],
    )
    return registry


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", DEFAULT_SKILL_TIMEOUT),
        ("bad", DEFAULT_SKILL_TIMEOUT),
        ("0", 1.0),
        ("-5", 1.0),
        ("30", 30.0),
        ("9999", MAX_SKILL_TIMEOUT),
    ],
)
def test_skill_timeout_config(monkeypatch, raw, expected):
    if raw:
        monkeypatch.setenv("MEDIX_SKILL_TIMEOUT", raw)
    else:
        monkeypatch.delenv("MEDIX_SKILL_TIMEOUT", raising=False)
    assert get_skill_timeout() == expected


def test_async_skill_timeout_returns_error(monkeypatch):
    monkeypatch.setenv("MEDIX_SKILL_TIMEOUT", "1")

    async def slow(text):
        await asyncio.sleep(2)
        return {"success": True, "text": text}

    registry = _registry_with(slow)
    started = time.perf_counter()
    result = asyncio.run(registry.execute("slow", text="x"))
    elapsed = time.perf_counter() - started

    assert result["success"] is False
    assert result["timeout"] is True
    assert "超时" in result["error"]
    assert elapsed < 1.5


def test_sync_skill_timeout_returns_error(monkeypatch):
    monkeypatch.setenv("MEDIX_SKILL_TIMEOUT", "1")

    def slow(text):
        time.sleep(2)
        return {"success": True, "text": text}

    registry = _registry_with(slow)
    loop = asyncio.new_event_loop()
    started = time.perf_counter()
    try:
        result = loop.run_until_complete(
            registry.execute("slow", text="x")
        )
        elapsed = time.perf_counter() - started
    finally:
        # 不等待执行器线程退出，模拟常驻事件循环下的真实返回时延
        loop.close()

    assert result["success"] is False
    assert result["timeout"] is True
    assert elapsed < 1.5


def test_skill_within_timeout_returns_result(monkeypatch):
    monkeypatch.setenv("MEDIX_SKILL_TIMEOUT", "5")

    async def quick(text):
        return {"success": True, "text": text}

    registry = _registry_with(quick)
    result = asyncio.run(registry.execute("slow", text="ok"))

    assert result == {"success": True, "text": "ok"}
