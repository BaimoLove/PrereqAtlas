"""健康入口、旧 /health 下线与方法白名单。"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infra.db import get_engine


def _request_id_from(response: object) -> str:
    data = response.json()  # type: ignore[attr-defined]
    value = uuid.UUID(data["request_id"])
    return str(value)


def test_live_returns_ok_and_does_not_touch_database(production_app: FastAPI) -> None:
    production_app.dependency_overrides[get_engine] = lambda: (_ for _ in ()).throw(
        AssertionError("live 不得依赖数据库")
    )
    try:
        with TestClient(production_app) as client:
            response = client.get("/health/live")
    finally:
        production_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_live_response_request_id_is_uuid(production_app: FastAPI) -> None:
    with TestClient(production_app) as client:
        response = client.get("/health/live")
    assert str(uuid.UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]


def test_old_health_is_404_without_redirect(production_app: FastAPI) -> None:
    with TestClient(production_app) as client:
        response = client.get("/health", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers
    payload = response.json()
    assert payload["code"] == "NOT_FOUND"
    assert payload["request_id"]


def test_unknown_path_is_404_error_contract(production_app: FastAPI) -> None:
    with TestClient(production_app) as client:
        response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    _request_id_from(response)


def test_ready_503_uses_error_contract(production_app: FastAPI) -> None:
    from sqlalchemy import create_engine

    unreachable = create_engine(
        "mysql+pymysql://root:invalid@127.0.0.1:1/prereq_atlas?connect_timeout=1",
        pool_pre_ping=True,
    )
    production_app.dependency_overrides[get_engine] = lambda: unreachable
    try:
        with TestClient(production_app, raise_server_exceptions=False) as client:
            response = client.get("/health/ready")
    finally:
        production_app.dependency_overrides.clear()
        unreachable.dispose()

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "SERVICE_UNAVAILABLE"
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert "mysql" not in response.text.lower()
    assert "invalid" not in response.text


def test_non_get_post_on_defined_route_returns_405(client: TestClient) -> None:
    for method in ("PUT", "PATCH", "DELETE", "POST", "OPTIONS"):
        response = client.request(method, "/health/live")
        assert response.status_code == 405, method
        assert response.json()["code"] == "METHOD_NOT_ALLOWED"


def test_method_override_header_and_query_do_not_convert(client: TestClient) -> None:
    headers = {"X-HTTP-Method-Override": "GET"}
    assert client.put("/health/live", headers=headers).status_code == 405
    assert client.put("/health/live?_method=GET").status_code == 405
    assert client.post("/health/live?_method=GET").status_code == 405


def test_head_has_no_body(client: TestClient) -> None:
    response = client.head("/health/live")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["X-Request-ID"]


def test_openapi_only_exposes_get_and_post_and_no_legacy_health(
    production_app: FastAPI,
) -> None:
    schema = production_app.openapi()
    methods: set[str] = set()
    for path_item in schema["paths"].values():
        methods.update(key.upper() for key in path_item)
    assert methods <= {"GET", "POST"}
    assert "/health" not in schema["paths"]
    assert "/health/live" in schema["paths"]
    assert "/health/ready" in schema["paths"]
