"""Windows 控制台编码处理的单元测试。"""
import sys

from core.console import ensure_utf8_stdio


class _FakeStream:
    def __init__(self):
        self.kwargs = None

    def reconfigure(self, **kwargs):
        self.kwargs = kwargs


def test_reconfigures_stdout_and_stderr(monkeypatch):
    stdout, stderr = _FakeStream(), _FakeStream()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    ensure_utf8_stdio()

    expected = {"encoding": "utf-8", "errors": "replace"}
    assert stdout.kwargs == expected
    assert stderr.kwargs == expected


def test_ignores_streams_without_reconfigure(monkeypatch):
    monkeypatch.setattr(sys, "stdout", object())
    monkeypatch.setattr(sys, "stderr", object())

    # 不应抛异常（例如测试框架替换成的 StringIO）
    ensure_utf8_stdio()
