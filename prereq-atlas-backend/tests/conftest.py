"""pytest 全局夹具：独立 MySQL 测试库、迁移与临时库工厂。

集成测试只有在 ``TEST_DATABASE_URL`` 指向独立 ``*_test`` 库时才运行；
未配置或误指开发库时夹具直接失败，不静默跳过，也不建立连接。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.infra.config import get_settings
from support.db import (
    apply_schema_sql,
    create_database,
    drop_database,
    scratch_name,
    upgrade_to_head,
)
from support.db_guard import resolve_test_database_url


def _server_url(url: URL) -> URL:
    """去掉库名连接到 MySQL 实例；``URL.set(database=None)`` 不会清除库名。"""
    return URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=None,
        query=dict(url.query),
    )


@pytest.fixture(scope="session")
def test_database_url() -> URL:
    url = resolve_test_database_url()
    server = create_engine(_server_url(url), isolation_level="AUTOCOMMIT")
    try:
        create_database(server, url.database or "")
        upgrade_to_head(url)
    finally:
        server.dispose()
    return url


@pytest.fixture(scope="session")
def server_engine(test_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(_server_url(test_database_url), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def test_engine(test_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(test_database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def test_session_factory(test_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture(scope="session")
def development_engine() -> Iterator[Engine]:
    """开发库只读连接；T03 的结构对照需要它作为 DDL 基准。"""
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


ScratchFactory = Callable[[str], tuple[URL, Engine]]


@pytest.fixture
def scratch_engine(server_engine: Engine) -> Iterator[ScratchFactory]:
    """按需创建临时 ``*_test`` 库，测试结束后释放引擎并删库。"""
    created: list[str] = []
    engines: list[Engine] = []

    def factory(prefix: str) -> tuple[URL, Engine]:
        name = scratch_name(prefix)
        create_database(server_engine, name)
        url = server_engine.url.set(database=name)
        engine = create_engine(url, pool_pre_ping=True)
        created.append(name)
        engines.append(engine)
        return url, engine

    yield factory

    for engine in engines:
        engine.dispose()
    for name in created:
        drop_database(server_engine, name)


@pytest.fixture
def migrated_database(scratch_engine: ScratchFactory) -> tuple[URL, Engine]:
    url, engine = scratch_engine("migrate")
    upgrade_to_head(url)
    return url, engine


@pytest.fixture
def ddl_database(scratch_engine: ScratchFactory) -> tuple[URL, Engine]:
    url, engine = scratch_engine("ddl")
    apply_schema_sql(engine)
    return url, engine
