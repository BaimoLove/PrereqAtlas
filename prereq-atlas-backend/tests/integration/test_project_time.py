"""全项目 UTC+8：通过真实连接与数据库时间默认值验收。"""
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from app.infra.config import get_settings
from app.infra.db import get_engine

pytestmark = pytest.mark.integration


@pytest.fixture
def application_engine(migrated_database, monkeypatch) -> Iterator[Engine]:
    url, _ = migrated_database
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    get_engine.cache_clear()
    engine = get_engine()
    try:
        yield engine
    finally:
        engine.dispose()
        get_engine.cache_clear()
        get_settings.cache_clear()


def test_project_connection_restores_beijing_timezone_on_pool_reuse(application_engine):
    with application_engine.connect() as connection:
        assert connection.scalar(text("SELECT @@session.time_zone")) == "+08:00"
        connection.execute(text("SET SESSION time_zone = '+00:00'"))
    with application_engine.connect() as connection:
        assert connection.scalar(text("SELECT @@session.time_zone")) == "+08:00"


def test_database_default_and_on_update_timestamps_are_beijing(application_engine):
    from datetime import datetime

    with application_engine.begin() as connection:
        # Unix 时刻固定为 2026-10-26 16:31:35Z，对应北京时间次日 00:31:35。
        connection.execute(text("SET timestamp = 1793032295"))
        connection.execute(text(
            "INSERT INTO app_user (username,password_hash) VALUES ('Time_Default','fixture-only')"
        ))
        created = connection.execute(text(
            "SELECT created_at,updated_at FROM app_user WHERE username='Time_Default'"
        )).one()
        assert created.created_at == datetime(2026, 10, 27, 0, 31, 35)
        assert created.updated_at == datetime(2026, 10, 27, 0, 31, 35)
        connection.execute(text("SET timestamp = 1793032355"))
        connection.execute(text("UPDATE app_user SET username='Time_Updated'"))
        updated = connection.scalar(text("SELECT updated_at FROM app_user"))
        assert updated == datetime(2026, 10, 27, 0, 32, 35)
