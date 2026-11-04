"""密钥脱敏与日志保护。

目标：即使模型 SDK 的异常文本、日志或调试输出里出现 API Key，
也只显示首尾少量字符，避免在终端、日志文件或接口响应中泄漏完整密钥。
"""
from __future__ import annotations

import os
import re
from typing import Optional

SECRET_ENV_NAMES = (
    "MEDIX_API_KEY",
    "MEM0_API_KEY",
    "MEDIX_ACCESS_TOKEN",
)

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
    """保留首尾少量字符，其余用 *** 替代。"""
    value = str(value or "")
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def redact_secrets(text: Optional[str]) -> str:
    """对文本中的环境变量密钥与常见 Key 格式做脱敏。"""
    if not text:
        return ""

    result = str(text)

    # 已知环境变量中的真实密钥优先替换（包括不含 sk- 前缀的令牌）
    for name in SECRET_ENV_NAMES:
        value = (os.getenv(name) or "").strip()
        if len(value) >= 6 and value in result:
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
