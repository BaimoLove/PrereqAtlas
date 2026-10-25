"""GET /health/ready 的契约：数据库可用性只在这里对外暴露。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.infra.config import get_settings
from app.infra.db import get_engine
from app.main import create_app

pytestmark = pytest.mark.integration


def require_database() -> None:
    if "change-me" in get_settings().database_url:
        pytest.skip("DATABASE_URL 仍是占位值，跳过集成测试")


def test_ready_reports_ok_when_database_reachable() -> None:
    require_database()
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"


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
