"""API 测试共用夹具：生产应用 + 仅测试使用的验证/异常路由。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from app.main import create_app
from support.sentinels import BEARER_SENTINEL, DB_PASSWORD_SENTINEL, PASSWORD_SENTINEL


class _ValidationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=20)


def _install_test_routes(app: FastAPI) -> None:
    @app.post("/_test/validate")
    def validate(body: _ValidationBody) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/_test/boom")
    def boom() -> dict[str, bool]:
        raise RuntimeError(
            f"password={PASSWORD_SENTINEL} Authorization: Bearer {BEARER_SENTINEL} "
            f"mysql+pymysql://root:{DB_PASSWORD_SENTINEL}@127.0.0.1:3306/db"
        )


@pytest.fixture
def production_app() -> FastAPI:
    return create_app()


@pytest.fixture
def app_with_test_routes() -> FastAPI:
    app = create_app()
    _install_test_routes(app)
    return app


@pytest.fixture
def client(app_with_test_routes: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_with_test_routes, raise_server_exceptions=False) as test_client:
        yield test_client
