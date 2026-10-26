"""数据库连接基础层的集成测试：独立 MySQL 测试库，不需要 ORM 模型。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.infra.db import session_scope

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _cleanup_tdd_rows(
    test_session_factory: sessionmaker[Session],
) -> Iterator[None]:
    """即使某个用例清理失败，模块结束后也不把测试数据留在测试库。"""
    yield
    with session_scope(test_session_factory) as session:
        session.execute(text("DELETE FROM app_user WHERE username LIKE 'tdd_%'"))


def test_session_scope_commits_row_visible_in_new_session(
    test_session_factory: sessionmaker[Session],
) -> None:
    username = f"tdd_{uuid.uuid4().hex[:12]}"
    try:
        with session_scope(test_session_factory) as session:
            session.execute(
                text("INSERT INTO app_user (username, password_hash) VALUES (:name, :hash)"),
                {"name": username, "hash": "argon2id$placeholder"},
            )
        with session_scope(test_session_factory) as session:
            stored = session.execute(
                text("SELECT username FROM app_user WHERE username = :name"),
                {"name": username},
            ).scalar_one()
    finally:
        with session_scope(test_session_factory) as session:
            session.execute(text("DELETE FROM app_user WHERE username = :name"), {"name": username})

    assert stored == username


def test_update_and_delete_are_committed(
    test_session_factory: sessionmaker[Session],
) -> None:
    username = f"tdd_{uuid.uuid4().hex[:12]}"
    renamed = f"{username}_renamed"
    try:
        with session_scope(test_session_factory) as session:
            session.execute(
                text("INSERT INTO app_user (username, password_hash) VALUES (:name, :hash)"),
                {"name": username, "hash": "argon2id$placeholder"},
            )

        with session_scope(test_session_factory) as session:
            session.execute(
                text("UPDATE app_user SET username = :new WHERE username = :old"),
                {"new": renamed, "old": username},
            )
        with session_scope(test_session_factory) as session:
            updated = session.execute(
                text("SELECT COUNT(*) FROM app_user WHERE username = :name"),
                {"name": renamed},
            ).scalar_one()

        with session_scope(test_session_factory) as session:
            session.execute(text("DELETE FROM app_user WHERE username = :name"), {"name": renamed})
        with session_scope(test_session_factory) as session:
            remaining = session.execute(
                text("SELECT COUNT(*) FROM app_user WHERE username = :name"),
                {"name": renamed},
            ).scalar_one()
    finally:
        with session_scope(test_session_factory) as session:
            session.execute(
                text("DELETE FROM app_user WHERE username IN (:old, :new)"),
                {"old": username, "new": renamed},
            )

    assert updated == 1
    assert remaining == 0


def test_session_scope_rolls_back_when_body_raises(
    test_session_factory: sessionmaker[Session],
) -> None:
    username = f"tdd_{uuid.uuid4().hex[:12]}"
    try:
        with session_scope(test_session_factory) as session:
            session.execute(
                text("INSERT INTO app_user (username, password_hash) VALUES (:name, :hash)"),
                {"name": username, "hash": "argon2id$placeholder"},
            )

        with (
            pytest.raises(RuntimeError, match="boom"),
            session_scope(test_session_factory) as session,
        ):
            session.execute(
                text("UPDATE app_user SET role = 'ADMIN' WHERE username = :name"),
                {"name": username},
            )
            raise RuntimeError("boom")

        with session_scope(test_session_factory) as session:
            role = session.execute(
                text("SELECT role FROM app_user WHERE username = :name"), {"name": username}
            ).scalar_one()
    finally:
        with session_scope(test_session_factory) as session:
            session.execute(text("DELETE FROM app_user WHERE username = :name"), {"name": username})

    assert role == "STUDENT"
