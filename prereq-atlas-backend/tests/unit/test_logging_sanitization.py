"""日志脱敏：口令、Bearer 令牌、数据库连接串不得进入日志。"""

from __future__ import annotations

import io
import logging

from app.infra.logging import SanitizingFormatter, configure_logging, sanitize_text

PASSWORD = "pw-sentinel-1234567890"
BEARER = "bearer-sentinel-token"
DB_PASSWORD = "db-sentinel-password"


def test_sanitize_text_redacts_password_and_bearer() -> None:
    raw = f"password={PASSWORD} Authorization: Bearer {BEARER} token={BEARER}"
    cleaned = sanitize_text(raw)
    for secret in (PASSWORD, BEARER):
        assert secret not in cleaned
    assert "password=***" in cleaned
    assert "***" in cleaned


def test_sanitize_text_redacts_database_url_password() -> None:
    raw = f"mysql+pymysql://root:{DB_PASSWORD}@127.0.0.1:3306/prereq_atlas"
    cleaned = sanitize_text(raw)
    assert DB_PASSWORD not in cleaned
    assert "root:***@" in cleaned


def test_sanitize_text_keeps_benign_content() -> None:
    raw = "request_id=abc method=GET path=/health/live status=200"
    assert sanitize_text(raw) == raw


def test_sanitizing_formatter_redacts_formatted_output() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SanitizingFormatter("%(message)s"))
    logger = logging.getLogger("test.sanitizing_formatter")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        "failed password=%s Authorization: Bearer %s mysql+pymysql://root:%s@host/db",
        PASSWORD,
        BEARER,
        DB_PASSWORD,
    )
    formatted = stream.getvalue()
    for secret in (PASSWORD, BEARER, DB_PASSWORD):
        assert secret not in formatted


def test_configure_logging_is_idempotent() -> None:
    from app.infra.config import Settings

    settings = Settings(_env_file=None)
    configure_logging(settings)
    first = len(logging.getLogger().handlers)
    configure_logging(settings)
    assert len(logging.getLogger().handlers) == first
