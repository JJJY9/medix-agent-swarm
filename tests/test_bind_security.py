"""未设置访问令牌时的绑定地址安全校验。"""
import os
import sys

import pytest

import api_server


def test_loopback_hosts_are_recognized():
    for host in ("127.0.0.1", "::1", "localhost", "LOCALHOST", "127.0.0.2"):
        assert api_server._is_loopback_host(host) is True
    for host in ("0.0.0.0", "192.168.1.10", "example.local", ""):
        assert api_server._is_loopback_host(host) is False


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "::1", "localhost"],
)
def test_loopback_bind_allowed_without_token(host):
    assert api_server.check_bind_security(host, "") is None


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "192.168.1.10", "example.local"],
)
def test_public_bind_requires_token(host):
    error = api_server.check_bind_security(host, "")
    assert error is not None
    assert "MEDIX_ACCESS_TOKEN" in error
    assert "--allow-insecure-lan" in error


def test_public_bind_allowed_with_token():
    assert api_server.check_bind_security("0.0.0.0", "secret-token") is None


def test_public_bind_allowed_with_explicit_opt_in():
    assert api_server.check_bind_security(
        "0.0.0.0", "", allow_insecure_lan=True
    ) is None


def test_main_refuses_public_bind_without_token(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv(api_server.INSECURE_LAN_ENV, raising=False)
    monkeypatch.setattr(
        sys, "argv",
        ["api_server.py", "--host", "0.0.0.0", "--port", "8123"],
    )

    with pytest.raises(SystemExit) as excinfo:
        api_server.main()

    assert excinfo.value.code == 2


def test_main_allows_public_bind_with_opt_in_flag(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv(api_server.INSECURE_LAN_ENV, raising=False)
    monkeypatch.setattr(
        sys, "argv",
        [
            "api_server.py", "--host", "0.0.0.0", "--port", "8123",
            "--allow-insecure-lan",
        ],
    )

    class StopServer(Exception):
        pass

    class FakeUvicorn:
        @staticmethod
        def run(*args, **kwargs):
            raise StopServer

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)

    try:
        with pytest.raises(StopServer):
            api_server.main()
    finally:
        # main() 会把命令行开关写入请求期环境变量：真实进程里这是
        # 期望行为，测试内必须清理，否则后续用例会以为已放行内网
        os.environ.pop(api_server.INSECURE_LAN_ENV, None)


def test_main_allows_public_bind_with_env_opt_in(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv(api_server.INSECURE_LAN_ENV, "1")
    monkeypatch.setattr(
        sys, "argv",
        ["api_server.py", "--host", "0.0.0.0", "--port", "8123"],
    )

    class StopServer(Exception):
        pass

    class FakeUvicorn:
        @staticmethod
        def run(*args, **kwargs):
            raise StopServer

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)

    with pytest.raises(StopServer):
        api_server.main()


def test_main_loopback_default_starts(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["api_server.py"])

    class StopServer(Exception):
        pass

    class FakeUvicorn:
        @staticmethod
        def run(*args, **kwargs):
            raise StopServer

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)

    with pytest.raises(StopServer):
        api_server.main()
