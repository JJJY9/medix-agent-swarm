"""控制台编码处理。

中文 Windows 上，stdout/stderr 被重定向（管道、CI、IDE 输出面板）时默认使用
GBK 编码，打印 emoji 或部分中文符号会抛 UnicodeEncodeError。统一在入口处把
标准流转成 UTF-8，避免日志和测试输出把程序打断。
"""
from __future__ import annotations

import sys

_FALLBACK_KWARGS = {"encoding": "utf-8", "errors": "replace"}


def ensure_utf8_stdio() -> None:
    """尽量把 stdin/stdout/stderr 切换为 UTF-8，不支持时静默跳过。

    管道输入中文时 stdin 默认 GBK+surrogateescape，会解码成乱码和
    孤立代理字符，后续请求编码直接抛 UnicodeEncodeError。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(**_FALLBACK_KWARGS)
        except (AttributeError, ValueError):
            # StringIO、被替换的测试流等没有 reconfigure，或者已经关闭
            continue
