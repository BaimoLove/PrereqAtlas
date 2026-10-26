"""日志与错误体不得出现口令、Bearer 令牌或数据库口令。"""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from support.sentinels import (
    BEARER_SENTINEL,
    DB_PASSWORD_SENTINEL,
    PASSWORD_SENTINEL,
    SHORT_PASSWORD_SENTINEL,
)


def test_500_response_and_logs_contain_no_secrets(
    client: TestClient, caplog: object
) -> None:
    caplog.set_level(logging.INFO)  # type: ignore[attr-defined]
    response = client.get("/_test/boom")

    combined = response.text + caplog.text  # type: ignore[attr-defined]
    for secret in (PASSWORD_SENTINEL, BEARER_SENTINEL, DB_PASSWORD_SENTINEL):
        assert secret not in combined, secret


def test_validation_response_contains_no_password_value(client: TestClient) -> None:
    response = client.post("/_test/validate", json={"password": SHORT_PASSWORD_SENTINEL})
    assert response.status_code == 422
    assert SHORT_PASSWORD_SENTINEL not in response.text


def test_405_with_authorization_header_logs_no_token(
    client: TestClient, caplog: object
) -> None:
    caplog.set_level(logging.INFO)  # type: ignore[attr-defined]
    response = client.put(
        "/health/live",
        headers={"Authorization": f"Bearer {BEARER_SENTINEL}"},
    )
    assert response.status_code == 405
    assert BEARER_SENTINEL not in response.text
    assert BEARER_SENTINEL not in caplog.text  # type: ignore[attr-defined]
