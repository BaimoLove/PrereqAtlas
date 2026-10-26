"""统一错误体、request_id 与日志一致性。"""

from __future__ import annotations

import logging
import uuid

from fastapi.testclient import TestClient


def test_validation_error_matches_contract(client: TestClient) -> None:
    response = client.post("/_test/validate", json={"password": "short"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["request_id"]
    assert isinstance(payload["details"], dict)
    errors = payload["details"]["errors"]
    assert errors
    assert errors[0]["field"] == "password"
    assert errors[0]["reason"]
    assert response.headers["X-Request-ID"] == payload["request_id"]


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    sentinel = "short-pw-value"
    response = client.post("/_test/validate", json={"password": sentinel})
    assert response.status_code == 422
    assert sentinel not in response.text


def test_invalid_json_uses_same_error_contract(client: TestClient) -> None:
    response = client.post(
        "/_test/validate",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["request_id"]


def test_unhandled_exception_returns_500_without_stack_and_logs_request_id(
    client: TestClient, caplog: object
) -> None:
    caplog.set_level(logging.INFO)  # type: ignore[attr-defined]
    response = client.get("/_test/boom")

    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "INTERNAL_ERROR"
    assert payload["request_id"]
    assert response.headers["X-Request-ID"] == payload["request_id"]
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text
    assert "password=" not in response.text
    assert payload["request_id"] in caplog.text  # type: ignore[attr-defined]


def test_error_request_id_is_uuid_and_stable_in_one_response(client: TestClient) -> None:
    response = client.get("/_test/boom")
    payload = response.json()
    request_id = str(uuid.UUID(payload["request_id"]))
    assert request_id == payload["request_id"]
    assert request_id == response.headers["X-Request-ID"]


def test_404_and_405_are_logged_with_request_id(
    client: TestClient, caplog: object
) -> None:
    caplog.set_level(logging.INFO)  # type: ignore[attr-defined]
    not_found = client.get("/missing-path")
    method_not_allowed = client.put("/health/live")

    assert not_found.json()["request_id"] in caplog.text  # type: ignore[attr-defined]
    assert method_not_allowed.json()["request_id"] in caplog.text  # type: ignore[attr-defined]


def test_405_does_not_create_business_writes(client: TestClient) -> None:
    """T04 阶段无业务写入口；405 必须只返回错误体，不产生副作用。"""
    response = client.post("/health/live", json={"unexpected": True})
    assert response.status_code == 405
    payload = response.json()
    assert set(payload) == {"code", "message", "details", "request_id"}
