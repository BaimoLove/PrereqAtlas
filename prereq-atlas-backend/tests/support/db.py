"""集成测试的数据库操作辅助：建库、迁移、执行 DDL、巡检与最小一致数据。"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.engine import URL

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = BACKEND_ROOT / "database" / "schema.sql"
INTEGRITY_SQL = BACKEND_ROOT / "database" / "integrity_checks.sql"

_SAFE_DATABASE_NAME = re.compile(r"^[a-z0-9_]+$")


def alembic_config(url: URL) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    return config


def upgrade_to_head(url: URL) -> None:
    command.upgrade(alembic_config(url), "head")


def downgrade_to_base(url: URL) -> None:
    command.downgrade(alembic_config(url), "base")


def create_database(server_engine: Engine, name: str) -> None:
    if not _SAFE_DATABASE_NAME.fullmatch(name):
        raise ValueError(f"非法库名：{name!r}")
    with server_engine.connect() as connection:
        connection.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{name}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        )


def drop_database(server_engine: Engine, name: str) -> None:
    if not _SAFE_DATABASE_NAME.fullmatch(name):
        raise ValueError(f"非法库名：{name!r}")
    with server_engine.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))


def scratch_name(prefix: str) -> str:
    return f"prereq_atlas_{prefix}_{uuid.uuid4().hex[:8]}_test"


def table_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE()"
            )
        ).scalars()
        return set(rows)


def row_count(engine: Engine, table: str) -> int:
    if not _SAFE_DATABASE_NAME.fullmatch(table):
        raise ValueError(f"非法表名：{table!r}")
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one())


def foreign_key_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.referential_constraints "
                    "WHERE constraint_schema = DATABASE()"
                )
            ).scalar_one()
        )


def read_alembic_version(engine: Engine) -> str | None:
    with engine.connect() as connection:
        exists = connection.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'alembic_version'"
            )
        ).scalar_one()
        if not exists:
            return None
        version = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        return str(version) if version is not None else None


def _statements(path: Path) -> list[str]:
    sql = path.read_text(encoding="utf-8")
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [statement.strip() for statement in body.split(";") if statement.strip()]


def apply_schema_sql(engine: Engine, path: Path = SCHEMA_SQL) -> None:
    """在已存在的空库执行 DDL；跳过脚本自带的建库/USE/SET NAMES。"""
    with engine.begin() as connection:
        for statement in _statements(path):
            head = statement.lstrip().upper()
            if head.startswith(("CREATE DATABASE", "USE ", "SET NAMES")):
                continue
            connection.execute(text(statement))


def run_integrity_checks(engine: Engine, path: Path = INTEGRITY_SQL) -> dict[str, int]:
    """执行完整性巡检，返回“检查名 -> 违规行数”。

    巡检脚本由若干条 SELECT 组成，其中第一条是多个引用的 UNION ALL，
    因此必须遍历每条语句的全部结果行，不能只取第一行。
    """
    results: dict[str, int] = {}
    with engine.connect() as connection:
        for statement in _statements(path):
            for row in connection.execute(text(statement)).all():
                name, count = row[0], int(row[1])
                results[str(name)] = count
    return results


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def insert_consistent_fixture(engine: Engine) -> dict[str, int]:
    """插入覆盖 15 张表的最小一致数据，供完整性巡检使用。"""
    ids: dict[str, int] = {}
    with engine.begin() as connection:
        def insert(sql: str, params: dict[str, Any]) -> int:
            result = connection.execute(text(sql), params)
            return int(result.lastrowid or 0)

        ids["admin"] = insert(
            "INSERT INTO app_user (username, password_hash, role) "
            "VALUES (:u, :h, 'ADMIN')",
            {"u": "fixture_admin", "h": "argon2id$fixture$admin"},
        )
        ids["student"] = insert(
            "INSERT INTO app_user (username, password_hash, role) "
            "VALUES (:u, :h, 'STUDENT')",
            {"u": "fixture_student", "h": "argon2id$fixture$student"},
        )
        ids["concept_a"] = insert(
            "INSERT INTO concept (code, source_namespace, source_key) "
            "VALUES ('FIX-A', 'fixture', 'N001')",
            {},
        )
        ids["concept_b"] = insert(
            "INSERT INTO concept (code, source_namespace, source_key) "
            "VALUES ('FIX-B', 'fixture', 'N002')",
            {},
        )
        ids["version"] = insert(
            "INSERT INTO graph_version "
            "(version_label, state, draft_revision, content_hash, validated_revision, "
            " created_by, published_by, published_at) "
            "VALUES ('fixture-v1', 'PUBLISHED', 1, :hash, 1, :admin, :admin, NOW(3))",
            {"hash": "a" * 64, "admin": ids["admin"]},
        )
        connection.execute(
            text(
                "INSERT INTO chapter_revision (version_id, chapter_code, name, display_order) "
                "VALUES (:version, 'M01', 'Fixture', 1)"
            ),
            {"version": ids["version"]},
        )
        for concept in (ids["concept_a"], ids["concept_b"]):
            connection.execute(
                text(
                    "INSERT INTO concept_revision "
                    "(version_id, concept_id, chapter_code, label_zh, node_type) "
                    "VALUES (:version, :concept, 'M01', :label, '基础概念')"
                ),
                {
                    "version": ids["version"],
                    "concept": concept,
                    "label": f"Fixture {concept}",
                },
            )
        ids["batch"] = insert(
            "INSERT INTO import_batch "
            "(operator_id, source_note, node_sha256, edge_sha256, status, report_json) "
            "VALUES (:admin, 'fixture', :node_hash, :edge_hash, 'STAGED', :report)",
            {
                "admin": ids["admin"],
                "node_hash": "b" * 64,
                "edge_hash": "c" * 64,
                "report": _json({"nodes": 1, "edges": 1}),
            },
        )
        connection.execute(
            text(
                "INSERT INTO import_raw_node "
                "(batch_id, source_node_key, logical_record_no, raw_json, mapping_status, "
                " mapped_concept_id) "
                "VALUES (:batch, 'N001', 2, :raw, 'MAPPED', :concept)"
            ),
            {
                "batch": ids["batch"],
                "raw": _json({"node_id": "N001"}),
                "concept": ids["concept_a"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO import_raw_edge "
                "(batch_id, source_edge_key, logical_record_no, source_node_key, "
                " target_node_key, structural_type, source_strength, raw_json) "
                "VALUES (:batch, 'E001', 2, 'N001', 'N002', 'direct', 1.00, :raw)"
            ),
            {"batch": ids["batch"], "raw": _json({"edge_id": "E001"})},
        )
        connection.execute(
            text(
                "INSERT INTO graph_relation "
                "(version_id, source_concept_id, target_concept_id, necessity, "
                " structural_type, source_strength, review_status, rationale, "
                " source_batch_id, source_edge_key, reviewed_by, reviewed_at) "
                "VALUES (:version, :source, :target, 'REQUIRED', 'direct', 1.00, "
                " 'APPROVED', 'fixture', :batch, 'E001', :admin, NOW(3))"
            ),
            {
                "version": ids["version"],
                "source": ids["concept_a"],
                "target": ids["concept_b"],
                "batch": ids["batch"],
                "admin": ids["admin"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO active_graph (singleton_id, version_id, revision, updated_by) "
                "VALUES (1, :version, 0, :admin)"
            ),
            {"version": ids["version"], "admin": ids["admin"]},
        )
        connection.execute(
            text(
                "INSERT INTO graph_audit "
                "(version_id, operator_id, action, revision_before, revision_after, summary) "
                "VALUES (:version, :admin, 'PUBLISH', 0, 1, 'fixture')"
            ),
            {"version": ids["version"], "admin": ids["admin"]},
        )
        connection.execute(
            text(
                "INSERT INTO user_mastery "
                "(user_id, concept_id, declared_version_id) "
                "VALUES (:student, :concept, :version)"
            ),
            {
                "student": ids["student"],
                "concept": ids["concept_a"],
                "version": ids["version"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO resource_revision (version_id, concept_id, title, url, source) "
                "VALUES (:version, :concept, 'Fixture resource', "
                " 'https://example.com/fixture', 'fixture')"
            ),
            {"version": ids["version"], "concept": ids["concept_a"]},
        )
        ids["plan"] = insert(
            "INSERT INTO review_plan "
            "(owner_id, graph_version_id, title, request_id, request_hash, schema_version, "
            " algorithm_version, input_snapshot, result_snapshot) "
            "VALUES (:student, :version, 'Fixture plan', :request_id, :hash, '1', '1', "
            " :input, :result)",
            {
                "student": ids["student"],
                "version": ids["version"],
                "request_id": str(uuid.uuid4()),
                "hash": "d" * 64,
                "input": _json({"target_ids": [ids["concept_b"]]}),
                "result": _json(
                    {"stages": [{"stage_index": 1, "concept_ids": [ids["concept_b"]]}]}
                ),
            },
        )
        connection.execute(
            text(
                "INSERT INTO review_plan_node "
                "(plan_id, graph_version_id, concept_id, stage_index) "
                "VALUES (:plan, :version, :concept, 1)"
            ),
            {
                "plan": ids["plan"],
                "version": ids["version"],
                "concept": ids["concept_b"],
            },
        )
    return ids
