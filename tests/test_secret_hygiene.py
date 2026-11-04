"""密钥脱敏与仓库卫生测试。

覆盖两条防线：
1. 运行时的日志、异常与接口错误信息不出现完整 API Key；
2. 仓库已跟踪文件中不存在真实密钥，且 .env 系列文件被忽略。
"""
import re
import subprocess
from pathlib import Path

from core.security import mask_secret, redact_secrets

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 运行时拼接，避免测试源码本身被密钥扫描规则命中
FAKE_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
BEARER_TOKEN = "abc.def-" + "1234567890xyz"

# 与 core/security.py 保持一致的常见密钥格式
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        "-----BEGIN " + "(?:RSA |OPENSSH |EC )?" + "PRIVATE KEY-----"
    ),
]


def git(*args):
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )


def test_mask_secret_keeps_only_edges():
    assert mask_secret(FAKE_KEY) == FAKE_KEY[:4] + "***" + FAKE_KEY[-4:]
    assert mask_secret("short") == "***"
    assert mask_secret(None) == "***"


def test_redact_secrets_masks_sk_key():
    result = redact_secrets(f"模型调用失败：{FAKE_KEY}，请重试")

    assert FAKE_KEY not in result
    assert "sk-a***k1L2" in result
    assert "模型调用失败" in result


def test_redact_secrets_masks_bearer_token():
    result = redact_secrets(f"Authorization: Bearer {BEARER_TOKEN}")

    assert BEARER_TOKEN not in result
    assert "Bearer ***" in result


def test_redact_secrets_masks_assignment():
    result = redact_secrets(f"请求参数 api_key={FAKE_KEY}")

    assert FAKE_KEY not in result
    assert "api_key=***" in result


def test_redact_secrets_masks_runtime_env_secret(monkeypatch):
    monkeypatch.setenv("MEDIX_API_KEY", "plain-custom-token-123456")

    result = redact_secrets("上游返回：plain-custom-token-123456 无效")

    assert "plain-custom-token-123456" not in result
    assert "plai***3456" in result


def test_redact_secrets_handles_empty_text():
    assert redact_secrets(None) == ""
    assert redact_secrets("") == ""
    assert redact_secrets("普通医学文本，与密钥无关") == "普通医学文本，与密钥无关"


def test_llm_error_message_is_redacted():
    from core.llm_client import LLMError

    error = LLMError("llm_error", f"调用失败：{FAKE_KEY}")

    assert FAKE_KEY not in str(error)
    assert FAKE_KEY not in error.user_message


def test_loguru_messages_are_redacted(tmp_path):
    from loguru import logger

    from core.security import install_log_redaction

    log_file = tmp_path / "redacted.log"
    sink_id = logger.add(log_file, format="{message}", encoding="utf-8")
    try:
        install_log_redaction()
        logger.info(f"LLM 请求失败，key={FAKE_KEY}")
    finally:
        logger.remove(sink_id)

    content = log_file.read_text(encoding="utf-8")
    assert FAKE_KEY not in content
    assert "***" in content


def test_http_error_details_are_redacted():
    import asyncio

    from starlette.exceptions import HTTPException
    from starlette.requests import Request

    import api_server

    exc = HTTPException(status_code=400, detail=f"请求失败：{FAKE_KEY}")
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    })

    response = asyncio.run(
        api_server.redacting_http_exception_handler(request, exc)
    )

    body = response.body.decode("utf-8")
    assert response.status_code == 400
    assert FAKE_KEY not in body
    assert "***" in body


def test_tracked_files_contain_no_secrets():
    files = git("ls-files").stdout.splitlines()
    assert files, "git ls-files 不应为空"

    hits = []
    for name in files:
        path = PROJECT_ROOT / name
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data or len(data) > 2_000_000:
            continue
        text = data.decode("utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                hits.append(f"{name}: {match.group(0)[:12]}…")

    assert hits == [], "已跟踪文件中发现疑似密钥，请立即移除并轮换：" + "；".join(hits)


def test_env_files_are_ignored():
    for name in (".env", ".env.local", ".env.production"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", name],
            cwd=PROJECT_ROOT,
        )
        assert result.returncode == 0, f"{name} 应被 .gitignore 忽略"

    example = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"],
        cwd=PROJECT_ROOT,
    )
    assert example.returncode == 1, ".env.example 应保持可提交"


def test_env_file_is_not_tracked():
    tracked = git("ls-files").stdout.splitlines()

    env_files = [
        name for name in tracked
        if name == ".env" or name.startswith(".env.")
    ]

    assert env_files == [".env.example"], f"只应跟踪 .env.example，实际：{env_files}"
