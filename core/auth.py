"""可选的访问令牌校验。

默认不开启：只有设置环境变量 ``MEDIX_ACCESS_TOKEN`` 后，``/api/*``
（``/api/status`` 除外）才要求请求携带令牌。用于把服务暴露到局域网时
防止他人调用模型、读取或删除会诊记录。

令牌优先从 ``X-Medix-Token`` 请求头读取，也接受
``Authorization: Bearer <token>``。不接受 ``?token=``：查询串会进入
uvicorn 访问日志与代理日志，等于明文记录令牌。
"""
from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import Request

TOKEN_ENV = "MEDIX_ACCESS_TOKEN"
HEADER_NAME = "X-Medix-Token"


def get_access_token() -> str:
    """返回当前配置的访问令牌；未配置时返回空字符串。"""
    return (os.getenv(TOKEN_ENV) or "").strip()


def _decode_header(value: str) -> str:
    """HTTP 头按 latin-1 解码；这里尝试还原浏览器发送的 UTF-8 字节。"""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def request_token(request: Request) -> Optional[str]:
    """从请求头中提取令牌。"""
    header = request.headers.get(HEADER_NAME)
    if header:
        return _decode_header(header).strip()

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return _decode_header(authorization[7:]).strip()

    return None


def is_authorized(request: Request) -> bool:
    """校验请求是否携带了正确的访问令牌。

    未配置令牌时始终放行；配置后使用 ``secrets.compare_digest``
    做常量时间比较，避免时序侧信道。
    """
    expected = get_access_token()
    if not expected:
        return True

    supplied = request_token(request)
    if not supplied:
        return False

    # compare_digest 对含非 ASCII 字符的 str 会抛 TypeError，
    # 统一按 UTF-8 字节比较，保证中文/emoji 令牌也能正常校验。
    return secrets.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    )
