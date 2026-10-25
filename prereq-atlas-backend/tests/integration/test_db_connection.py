"""数据库连接基础层的集成测试：真实 MySQL，不需要 ORM 模型。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.infra.config import get_settings
from app.infra.db import session_scope

pytestmark = pytest.mark.integration


def require_database() -> None:
    """未配置真实连接串时跳过，避免把“没配库”误报成功能失败。"""
    if "change-me" in get_settings().database_url:
        pytest.skip("DATABASE_URL 仍是占位值，跳过集成测试")


def test_session_scope_commits_row_visible_in_new_session() -> None:
    require_database()
    username = f"tdd_{uuid.uuid4().hex[:12]}"
    try:
        with session_scope() as session:
            session.execute(
                text("INSERT INTO app_user (username, password_hash) VALUES (:name, :hash)"),
                {"name": username, "hash": "argon2id$placeholder"},
            )
        with session_scope() as session:
            stored = session.execute(
                text("SELECT username FROM app_user WHERE username = :name"),
                {"name": username},
            ).scalar_one()
    finally:
        with session_scope() as session:
            session.execute(text("DELETE FROM app_user WHERE username = :name"), {"name": username})

    assert stored == username


def test_update_and_delete_are_committed() -> None:
    require_database()
    username = f"tdd_{uuid.uuid4().hex[:12]}"
    renamed = f"{username}_renamed"
    try:
        with session_scope() as session:
            session.execute(
                text("INSERT INTO app_user (username, password_hash) VALUES (:name, :hash)"),
                {"name": username, "hash": "argon2id$placeholder"},
            )

        with session_scope() as session:
            session.execute(
                text("UPDATE app_user SET username = :new WHERE username = :old"),
                {"new": renamed, "old": username},
            )
        with session_scope() as session:
            updated = session.execute(
                text("SELECT COUNT(*) FROM app_user WHERE username = :name"),
                {"name": renamed},
            ).scalar_one()

        with session_scope() as session:
            session.execute(text("DELETE FROM app_user WHERE username = :name"), {"name": renamed})
        with session_scope() as session:
            remaining = session.execute(
                text("SELECT COUNT(*) FROM app_user WHERE username = :name"),
                {"name": renamed},
            ).scalar_one()
    finally:
        with session_scope() as session:
            session.execute(
                text("DELETE FROM app_user WHERE username IN (:old, :new)"),
                {"old": username, "new": renamed},
            )

    assert updated == 1
    assert remaining == 0


def test_session_scope_rolls_back_when_body_raises() -> None:
    require_database()
    username = f"tdd_{uuid.uuid4().hex[:12]}"
    try:
        with session_scope() as session:
            session.execute(
                text("INSERT INTO app_user (username, password_hash) VALUES (:name, :hash)"),
                {"name": username, "hash": "argon2id$placeholder"},
            )

        with pytest.raises(RuntimeError, match="boom"), session_scope() as session:
            session.execute(
                text("UPDATE app_user SET role = 'ADMIN' WHERE username = :name"),
                {"name": username},
            )
            raise RuntimeError("boom")

        with session_scope() as session:
            role = session.execute(
                text("SELECT role FROM app_user WHERE username = :name"), {"name": username}
            ).scalar_one()
    finally:
        with session_scope() as session:
            session.execute(text("DELETE FROM app_user WHERE username = :name"), {"name": username})

    assert role == "STUDENT"
