"""T15/T16 公共 HTTP 测试基座，所有写入只发生在临时迁移库。"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.common.http_contracts import RegisterRequest
from app.infra import db
from app.infra.config import get_settings
from app.main import create_app
from app.modules.identity.service import create_admin


class AuthHeaders(dict):
    def __repr__(self):
        return "<authentication headers redacted>"


@pytest.fixture
def publication_client(migrated_database, monkeypatch) -> Iterator:
    _, engine = migrated_database
    monkeypatch.setenv("JWT_SECRET", "publication-tests-only-signing-key-32")
    get_settings.cache_clear()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db, "get_session_factory", lambda: factory)
    app = create_app()
    app.dependency_overrides[db.get_engine] = lambda: engine
    credentials = {"username": "Publish_Admin", "password": "Publish-pass-123"}
    with factory() as session:
        create_admin(session, RegisterRequest(**credentials))
        session.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            login = client.post("/api/v1/auth/token", data=credentials)
            assert login.status_code == 200
            headers = AuthHeaders(Authorization=f"Bearer {login.json()['access_token']}")
            yield client, engine, headers
    finally:
        get_settings.cache_clear()
