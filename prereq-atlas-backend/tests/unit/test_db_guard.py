"""测试库守卫：未配置、误指开发库或系统库时必须在连接前失败。"""

from __future__ import annotations

import pytest

from support.db_guard import (
    DatabaseGuardError,
    is_test_database_name,
    resolve_test_database_url,
)

DEV_URL = "mysql+pymysql://user:secret@127.0.0.1:3306/prereq_atlas"
VALID_URL = "mysql+pymysql://user:secret@127.0.0.1:3306/prereq_atlas_test"


def test_missing_test_database_url_fails() -> None:
    with pytest.raises(DatabaseGuardError, match="TEST_DATABASE_URL"):
        resolve_test_database_url({}, development_url=DEV_URL)


def test_placeholder_test_database_url_fails() -> None:
    with pytest.raises(DatabaseGuardError, match="占位"):
        resolve_test_database_url(
            {"TEST_DATABASE_URL": "mysql+pymysql://u:change-me@h/prereq_atlas_test"},
            development_url=DEV_URL,
        )


def test_test_database_must_not_equal_development_database() -> None:
    with pytest.raises(DatabaseGuardError, match="开发库"):
        resolve_test_database_url({"TEST_DATABASE_URL": DEV_URL}, development_url=DEV_URL)


def test_test_database_must_end_with_test_suffix() -> None:
    with pytest.raises(DatabaseGuardError, match="_test"):
        resolve_test_database_url(
            {"TEST_DATABASE_URL": "mysql+pymysql://u:p@h/prereq_atlas_ci"},
            development_url=DEV_URL,
        )


@pytest.mark.parametrize("name", ["mysql", "information_schema", "performance_schema", "sys"])
def test_system_databases_are_rejected(name: str) -> None:
    with pytest.raises(DatabaseGuardError, match="系统库"):
        resolve_test_database_url(
            {"TEST_DATABASE_URL": f"mysql+pymysql://u:p@h/{name}"},
            development_url=DEV_URL,
        )


def test_non_mysql_database_is_rejected() -> None:
    with pytest.raises(DatabaseGuardError, match="MySQL"):
        resolve_test_database_url(
            {"TEST_DATABASE_URL": "sqlite:///prereq_atlas_test"},
            development_url=DEV_URL,
        )


def test_missing_database_name_is_rejected() -> None:
    with pytest.raises(DatabaseGuardError, match="库名"):
        resolve_test_database_url(
            {"TEST_DATABASE_URL": "mysql+pymysql://u:p@h"}, development_url=DEV_URL
        )


def test_valid_test_database_is_accepted() -> None:
    url = resolve_test_database_url({"TEST_DATABASE_URL": VALID_URL}, development_url=DEV_URL)
    assert url.database == "prereq_atlas_test"
    assert is_test_database_name(url.database)
    assert is_test_database_name("prereq_atlas_stamp_abcd_test")
    assert not is_test_database_name("prereq_atlas")

