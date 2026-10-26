"""T03：首版迁移、结构对照、独立测试库与安全登记基线。"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import URL, make_url

from app.infra.config import get_settings
from app.infra.metadata import metadata
from app.infra.schema_guard import (
    BaselineMismatch,
    diff_database_to_metadata,
    diff_schemas,
    read_schema,
    stamp_baseline,
)
from support.db import (
    foreign_key_count,
    insert_consistent_fixture,
    read_alembic_version,
    row_count,
    run_integrity_checks,
    table_names,
    upgrade_to_head,
)
from support.db_guard import is_test_database_name

BUSINESS_TABLES = set(metadata.tables)

EXPECTED_UNIQUE_KEYS = {
    ("app_user", "uk_user_username"),
    ("concept", "uk_concept_code"),
    ("concept", "uk_concept_source"),
    ("graph_version", "uk_graph_version_label"),
    ("graph_relation", "uk_graph_relation_edge"),
    ("import_raw_node", "uk_import_node_line"),
    ("import_raw_edge", "uk_import_edge_line"),
    ("review_plan", "uk_review_plan_request"),
}

EXPECTED_CHECK_NAMES = {
    "ck_user_role",
    "ck_concept_source_pair",
    "ck_graph_version_published",
    "ck_graph_relation_no_self",
    "ck_graph_relation_reviewed",
    "ck_import_node_mapped",
    "ck_resource_revision_url",
    "ck_plan_node_stage",
}


def test_fresh_mysql_upgrade_creates_all_business_tables(
    migrated_database: tuple[URL, Engine],
) -> None:
    _, engine = migrated_database
    actual = table_names(engine)
    assert actual - {"alembic_version"} == BUSINESS_TABLES
    assert len(BUSINESS_TABLES) == 15
    assert read_alembic_version(engine) is not None


def test_structure_matches_ddl_reference_and_metadata(
    migrated_database: tuple[URL, Engine], development_engine: Engine
) -> None:
    _, engine = migrated_database
    migrated = read_schema(engine)
    reference = read_schema(development_engine)

    assert diff_schemas(migrated, reference) == ()
    assert diff_database_to_metadata(engine) == ()

    # 关键结构点单独断言，避免“差异 0”掩盖漏项。
    assert migrated.columns[("app_user", "username_normalized")].computed == "lower(trim(username))"
    assert migrated.columns[("app_user", "id")].auto_increment is True
    assert migrated.columns[("active_graph", "singleton_id")].auto_increment is False
    for table in BUSINESS_TABLES:
        assert (table, "PRIMARY") in migrated.indexes
    assert set(migrated.indexes) >= EXPECTED_UNIQUE_KEYS
    assert {name for _, name in migrated.checks} >= EXPECTED_CHECK_NAMES


def test_no_foreign_keys_and_integrity_checks_pass(
    migrated_database: tuple[URL, Engine],
) -> None:
    _, engine = migrated_database
    assert foreign_key_count(engine) == 0
    insert_consistent_fixture(engine)
    for table in BUSINESS_TABLES:
        assert row_count(engine, table) >= 1, table

    results = run_integrity_checks(engine)
    assert len(results) == 31
    assert "review_plan_node.graph_version_id = review_plan.graph_version_id" in results
    assert "active_graph points to a PUBLISHED version" in results
    assert {name: count for name, count in results.items() if count != 0} == {}


def test_repeated_upgrade_head_changes_nothing(
    migrated_database: tuple[URL, Engine],
) -> None:
    url, engine = migrated_database
    before = read_schema(engine)
    version_before = read_alembic_version(engine)

    upgrade_to_head(url)  

    assert read_schema(engine) == before
    assert read_alembic_version(engine) == version_before


def test_isolated_test_database_is_not_development(test_database_url: URL) -> None:
    url = test_database_url
    assert is_test_database_name(url.database)  
    assert url.database != make_url(get_settings().database_url).database  


def test_stamp_consistent_prebuilt_database_preserves_rows(
    ddl_database: tuple[URL, Engine],
) -> None:
    url, engine = ddl_database
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app_user (username, password_hash, role) "
                "VALUES ('stamp_keep', 'argon2id$fixture', 'STUDENT')"
            )
        )
    before = row_count(engine, "app_user")

    stamp_baseline(url)  

    assert read_alembic_version(engine) is not None
    assert row_count(engine, "app_user") == before
    assert diff_database_to_metadata(engine) == ()


def test_stamp_refuses_inconsistent_database_and_preserves_rows(
    ddl_database: tuple[URL, Engine],
) -> None:
    url, engine = ddl_database
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app_user (username, password_hash, role) "
                "VALUES ('stamp_refuse', 'argon2id$fixture', 'STUDENT')"
            )
        )
        connection.execute(text("ALTER TABLE app_user ADD COLUMN unexpected_flag INT NULL"))
    before = row_count(engine, "app_user")

    with pytest.raises(BaselineMismatch) as exc_info:
        stamp_baseline(url)  

    assert any("unexpected_flag" in diff for diff in exc_info.value.diffs)
    assert read_alembic_version(engine) is None
    assert row_count(engine, "app_user") == before
