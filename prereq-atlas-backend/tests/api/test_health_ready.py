"""GET /health/ready 的契约：数据库可用性只在这里对外暴露。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from app.infra.db import get_engine
from app.main import create_app

pytestmark = pytest.mark.integration


def test_ready_reports_ok_when_database_reachable(test_engine: Engine) -> None:
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: test_engine
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["database"] == "ok"


def test_ready_reports_503_when_database_unreachable() -> None:
    unreachable = create_engine(
        "mysql+pymysql://root:invalid@127.0.0.1:1/prereq_atlas?connect_timeout=1",
        pool_pre_ping=True,
    )
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: unreachable
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()
        unreachable.dispose()

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "SERVICE_UNAVAILABLE"
    assert payload["request_id"]
    assert "invalid" not in response.text
