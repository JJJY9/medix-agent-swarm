"""滑动窗口限流的离线测试。"""
from core.ratelimit import SlidingWindowLimiter, get_rate_limit


def test_limiter_allows_up_to_limit_then_blocks():
    limiter = SlidingWindowLimiter(2, window=60)

    assert limiter.check("a", now=0) == (True, 0.0)
    assert limiter.check("a", now=1) == (True, 0.0)

    allowed, retry_after = limiter.check("a", now=2)
    assert allowed is False
    assert 58 <= retry_after <= 60

    # 不同 key 互不影响
    assert limiter.check("b", now=2)[0] is True

    # 窗口滑过后恢复
    assert limiter.check("a", now=61)[0] is True


def test_limiter_disabled_when_limit_is_zero():
    limiter = SlidingWindowLimiter(0)

    for _ in range(100):
        assert limiter.check("a")[0] is True


def test_limiter_clear():
    limiter = SlidingWindowLimiter(1)
    assert limiter.check("a")[0] is True
    assert limiter.check("a")[0] is False

    limiter.clear()

    assert limiter.check("a")[0] is True


def test_get_rate_limit_from_env(monkeypatch):
    monkeypatch.delenv("MEDIX_RATE_LIMIT", raising=False)
    assert get_rate_limit() == 30

    monkeypatch.setenv("MEDIX_RATE_LIMIT", "5")
    assert get_rate_limit() == 5

    monkeypatch.setenv("MEDIX_RATE_LIMIT", "0")
    assert get_rate_limit() == 0

    monkeypatch.setenv("MEDIX_RATE_LIMIT", "bad")
    assert get_rate_limit() == 30
