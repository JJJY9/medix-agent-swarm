"""密钥脱敏与日志保护。

目标：即使模型 SDK 的异常文本、日志或调试输出里出现 API Key，
也只显示首尾少量字符，避免在终端、日志文件或接口响应中泄漏完整密钥。
"""
from __future__ import annotations

import os
import re
import sys
import logging
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


#: URL 查询串（含 ? 后的全部内容），只保留主机与路径便于排障
_URL_QUERY_RE = re.compile(r"(\?)[^\s'\"\)>\]]+")


def redact_url_queries(text: Optional[str]) -> str:
    """把文本里 URL 的查询串替换为 ``<redacted>``。

    搜索/抓取失败时，HTTP 客户端异常文本常带完整请求 URL；查询串里
    可能就是患者的疾病与用药问题，不能原样写进日志。
    """
    if not text:
        return ""
    return _URL_QUERY_RE.sub(r"\1<redacted>", str(text))


def sanitize_base_url(value: Optional[str]) -> str:
    """对外展示的服务地址：去掉账号密码、查询参数与片段并统一脱敏。"""
    text = str(value or "")
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        # 连 URL 都无法拆分：不能 fail-open 回显原文
        return "***"
    # 非法端口（如 :8000x）会拒绝 userinfo：先取出去掉账号密码的 netloc
    hostless = parsed.netloc.rsplit("@", 1)[-1]
    try:
        # .port 是惰性解析，非法端口/占位符会在这里抛 ValueError
        port = parsed.port
    except ValueError:
        # 端口笔误时仍展示主机与路径，但必须丢弃 userinfo 与查询串
        # （可能含网关 Basic Auth 密码或 ?token=），不能原样回显
        sanitized = urlunsplit(
            (parsed.scheme, hostless, parsed.path, "", "")
        )
        return redact_secrets(sanitized)
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
                record["message"] = redact_url_queries(
                    redact_secrets(record["message"])
                )
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
    install_access_log_query_filter()


class _AccessLogQueryFilter(logging.Filter):
    """脱敏 uvicorn 访问日志中的查询串与患者/文档路径片段。"""

    #: 这些路径把患者姓名或文件名放在路径中，必须整体替换
    _SENSITIVE_PATH_RE = re.compile(
        r"(/api/(?:profiles|knowledge/documents)/)[^/?]+"
    )

    @classmethod
    def _redact_path(cls, path: str) -> str:
        redacted = cls._SENSITIVE_PATH_RE.sub(r"\1<redacted>", path)
        if "?" in redacted:
            redacted = redacted.split("?", 1)[0] + "?<redacted>"
        return redacted

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if isinstance(args, tuple) and len(args) >= 3:
                path = args[2]
                if isinstance(path, str):
                    redacted = self._redact_path(path)
                    if redacted != path:
                        updated = list(args)
                        updated[2] = redacted
                        record.args = tuple(updated)
        except Exception:
            pass
        return True


def install_access_log_query_filter() -> None:
    """给 uvicorn.access 安装查询串脱敏过滤器（可重复调用）。"""
    try:
        access_logger = logging.getLogger("uvicorn.access")
        if not any(
            isinstance(item, _AccessLogQueryFilter)
            for item in access_logger.filters
        ):
            access_logger.addFilter(_AccessLogQueryFilter())
    except Exception:
        pass
