"""统一日志配置与敏感信息脱敏。

日志只记录方法、路径、状态码、request_id 与异常类型；不记录请求体、请求头
或数据库连接串。即使调用方传入含秘密的文本，格式化器也会再次脱敏。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from app.common.time import BEIJING_TZ
from app.infra.config import Settings

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|jwt_secret|secret|token)"
            r"(\s*[=:]\s*)('[^']*'|\"[^\"]*\"|[^\s,;]+)"
        ),
        r"\1\2***",
    ),
    (
        re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer\s+)?[^\s,;]+"),
        "Authorization: ***",
    ),
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+"), "Bearer ***"),
    (
        re.compile(r"(?i)(mysql(?:\+pymysql)?://[^:/\s]+:)([^@\s]+)(@)"),
        r"\1***\3",
    ),
)

_HANDLER_MARKER = "_prereq_atlas_handler"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class BeijingFormatter(logging.Formatter):
    """将标准 LogRecord Unix 时间戳格式化为明确标注 UTC+8 的本地文本。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        value = datetime.fromtimestamp(record.created, tz=BEIJING_TZ)
        return value.isoformat(timespec="milliseconds")


def sanitize_text(value: str) -> str:
    """对文本中的口令、Bearer 令牌与数据库 URL 口令做脱敏。"""
    cleaned = value
    for pattern, replacement in _PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


class SanitizingFormatter(BeijingFormatter):
    """在输出前对完整格式化结果（含异常文本）再脱敏一次。"""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize_text(super().format(record))


def configure_logging(settings: Settings) -> None:
    """配置进程日志；重复调用不会叠加 handler。"""
    level = logging.DEBUG if settings.app_env.strip().lower() == "dev" else logging.INFO
    root = logging.getLogger()
    existing = [handler for handler in root.handlers if getattr(handler, _HANDLER_MARKER, False)]
    formatter = SanitizingFormatter(_LOG_FORMAT)
    if not existing:
        new_handler = logging.StreamHandler()
        setattr(new_handler, _HANDLER_MARKER, True)
        new_handler.setFormatter(formatter)
        root.addHandler(new_handler)
    else:
        for existing_handler in existing:
            existing_handler.setFormatter(formatter)
    root.setLevel(level)
