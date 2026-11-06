"""密钥脱敏与日志保护。

目标：即使模型 SDK 的异常文本、日志或调试输出里出现 API Key，
也只显示首尾少量字符，避免在终端、日志文件或接口响应中泄漏完整密钥。
"""
from __future__ import annotations

import os
import re
import sys
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

SECRET_ENV_NAMES = (
    "MEDIX_API_KEY",
    "MEM0_API_KEY",
    "MEDIX_ACCESS_TOKEN",
)

_RUNTIME_SECRETS: set[str] = set()


def register_secret(value: Optional[str]) -> None:
    """登记来自 config.py 等非环境变量的密钥，供脱敏使用。"""
    text = str(value or "").strip()
    if len(text) >= 6:
        _RUNTIME_SECRETS.add(text)


def _known_secrets() -> list[str]:
    values = set(_RUNTIME_SECRETS)
    for name in SECRET_ENV_NAMES:
        value = (os.getenv(name) or "").strip()
        if len(value) >= 6:
            values.add(value)
    return sorted(values, key=len, reverse=True)

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)

_BEARER_PATTERN = re.compile(
    r"(?i)(Bearer\s+)([A-Za-z0-9._\-]{12,})"
)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|secret)\s*[=:]\s*)([^\s,;\"']+)"
)


def mask_secret(value: str) -> str:
    """保留首尾少量字符，其余用 *** 替代；短密钥完全隐藏。"""
    value = str(value or "")
    if len(value) < 16:
        # 9-11 位密钥若保留首尾 4 位，等于暴露大半明文
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def sanitize_base_url(value: Optional[str]) -> str:
    """对外展示的服务地址：去掉账号密码、查询参数与片段并统一脱敏。"""
    text = str(value or "")
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        # .port 是惰性解析，非法端口（如 :8000x）会在这里抛 ValueError
        port = parsed.port
    except ValueError:
        return redact_secrets(text)
    host = parsed.hostname or ""
    if port:
        host = f"{host}:{port}"
    sanitized = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    return redact_secrets(sanitized)


def redact_secrets(text: Optional[str]) -> str:
    """对文本中的环境变量密钥与常见 Key 格式做脱敏。"""
    if not text:
        return ""

    result = str(text)

    # 已知环境变量/运行时登记的密钥优先替换（包括不含 sk- 前缀的令牌）
    for value in _known_secrets():
        if value in result:
            result = result.replace(value, mask_secret(value))

    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda match: mask_secret(match.group(0)), result)

    result = _BEARER_PATTERN.sub(
        lambda match: f"{match.group(1)}***", result
    )
    result = _ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}***", result
    )
    return result


def install_log_redaction() -> None:
    """给 loguru 安装消息脱敏 patcher；失败时静默跳过。"""
    try:
        from loguru import logger

        def _patcher(record):
            try:
                record["message"] = redact_secrets(record["message"])
            except Exception:
                pass

        logger.configure(patcher=_patcher)
    except Exception:
        pass


_server_logging_configured = False


def configure_server_logging() -> None:
    """配置 API 服务日志的安全默认值。

    loguru 默认 handler 使用 DEBUG 级别且 ``diagnose=True``，未捕获异常的
    回溯会打印栈帧局部变量（请求体、问诊上下文），把患者内容写进日志。
    这里显式关闭 backtrace/diagnose，并默认使用 INFO 级别。
    """
    global _server_logging_configured
    if _server_logging_configured:
        return
    _server_logging_configured = True
    logger = None
    try:
        from loguru import logger

        level = (os.getenv("MEDIX_LOG_LEVEL") or "INFO").strip().upper()
        try:
            # 先校验级别，避免 remove 之后 add 失败导致日志全部丢失
            logger.level(level or "INFO")
        except ValueError:
            level = "INFO"
        logger.remove()
        logger.add(
            sys.stderr,
            level=level or "INFO",
            backtrace=False,
            diagnose=False,
        )
    except Exception:
        # 配置失败时至少保留一个可用的 INFO handler
        if logger is not None:
            try:
                logger.add(
                    sys.stderr,
                    level="INFO",
                    backtrace=False,
                    diagnose=False,
                )
            except Exception:
                pass
