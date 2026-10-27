"""数据库结构对照与安全登记基线。

用途：
- 测试用独立库升级后与开发库 DDL 结构逐项对照；
- 已按 DDL 建表的库在登记 Alembic 基线前，先证明结构与 ORM 元数据一致；
- 结构不一致时拒绝 stamp，绝不先写 ``alembic_version``。

比较范围：表、列类型/可空/默认值/生成列/自增/注释、索引与唯一键、CHECK、
表引擎与排序规则。逻辑引用由 ``database/integrity_checks.sql`` 另行巡检。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, Engine, MetaData, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import URL
from sqlalchemy.sql.schema import Table

from app.infra.db import create_database_engine
from app.infra.metadata import metadata as default_metadata

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_VERSION_TABLE = "alembic_version"


class BaselineMismatch(RuntimeError):
    """结构与 ORM 元数据不一致；拒绝登记基线。"""

    def __init__(self, diffs: tuple[str, ...]) -> None:
        self.diffs = diffs
        super().__init__("数据库结构与 ORM 元数据不一致：" + "; ".join(diffs))


@dataclass(frozen=True, slots=True)
class ColumnShape:
    table: str
    name: str
    type: str
    nullable: bool
    default: str | None
    on_update: str | None
    auto_increment: bool
    computed: str | None
    comment: str


@dataclass(frozen=True, slots=True)
class IndexShape:
    table: str
    name: str
    columns: tuple[str, ...]
    unique: bool


@dataclass(frozen=True, slots=True)
class CheckShape:
    table: str
    name: str
    clause: str


@dataclass(frozen=True, slots=True)
class TableShape:
    name: str
    engine: str
    collation: str
    comment: str


@dataclass(frozen=True, slots=True)
class SchemaShape:
    tables: dict[str, TableShape]
    columns: dict[tuple[str, str], ColumnShape]
    indexes: dict[tuple[str, str], IndexShape]
    checks: dict[tuple[str, str], CheckShape]


def normalize_type(value: str) -> str:
    normalized = value.lower().replace(" ", "")
    normalized = normalized.replace("boolean", "tinyint(1)")
    normalized = normalized.replace("bool", "tinyint(1)")
    normalized = normalized.replace("numeric", "decimal")
    normalized = normalized.replace("integer", "int")
    # 字符集/排序规则由表级选项与 DB 对照单独覆盖，列类型比较不重复计入。
    normalized = re.sub(r"characterset[a-z0-9_]+", "", normalized)
    normalized = re.sub(r"collate[a-z0-9_]+", "", normalized)
    return normalized


def normalize_default(value: str | None) -> tuple[str | None, str | None]:
    """把 ``DEFAULT`` 与 ``ON UPDATE`` 拆开，并去掉 VARCHAR 默认值外层引号。"""
    if value is None:
        return None, None
    raw = value.strip()
    if not raw:
        return None, None
    parts = re.split(r"\s+on\s+update\s+", raw, maxsplit=1, flags=re.IGNORECASE)
    base = parts[0].strip()
    if len(base) >= 2 and base[0] == "'" and base[-1] == "'":
        base = base[1:-1]
    on_update = parts[1].strip() if len(parts) > 1 else None
    return (base or None), (on_update or None)


def normalize_clause(value: str) -> str:
    """把 MySQL 回显的 CHECK 与 ORM 的 SQL 文本归一为可比较的记号序列。

    MySQL 会在 AND 组外补冗余括号，ORM 不会；这里忽略括号，只比较条件记号。
    更强的逐字比较由“迁移库 vs DDL 库”的信息模式对照承担。
    """
    clause = value.lower().replace("`", "").replace("_utf8mb4", "")
    clause = clause.replace("\\'", "'")
    clause = clause.replace("(", "").replace(")", "")
    return re.sub(r"\s+", "", clause)


def normalize_expression(value: str) -> str:
    return re.sub(r"\s+", "", value.lower().replace("`", ""))


def _table_option(table: Table, key: str, default: str) -> str:
    options = table.dialect_options["mysql"]
    value = options.get(key)
    return str(value) if value is not None else default


def metadata_schema(metadata: MetaData = default_metadata) -> SchemaShape:
    dialect = mysql.dialect()
    tables: dict[str, TableShape] = {}
    columns: dict[tuple[str, str], ColumnShape] = {}
    indexes: dict[tuple[str, str], IndexShape] = {}
    checks: dict[tuple[str, str], CheckShape] = {}

    for table in metadata.sorted_tables:
        tables[table.name] = TableShape(
            name=table.name,
            engine=_table_option(table, "engine", "InnoDB"),
            collation=_table_option(table, "collate", "utf8mb4_0900_ai_ci"),
            comment=table.comment or "",
        )
        for column in table.columns:
            server_default = None
            if column.computed is None and column.server_default is not None:
                default_arg = getattr(column.server_default, "arg", None)
                if default_arg is not None:
                    server_default = str(default_arg)
            default, on_update = normalize_default(server_default)
            auto_increment = bool(
                column.autoincrement is True
                or (
                    column.autoincrement == "auto"
                    and column.primary_key
                    and len(table.primary_key.columns) == 1
                )
            )
            computed = (
                normalize_expression(str(column.computed.sqltext))
                if column.computed is not None
                else None
            )
            columns[(table.name, column.name)] = ColumnShape(
                table=table.name,
                name=column.name,
                type=normalize_type(str(column.type.compile(dialect=dialect))),
                nullable=bool(column.nullable),
                default=default,
                on_update=on_update,
                auto_increment=auto_increment,
                computed=computed,
                comment=column.comment or "",
            )

        primary_columns = tuple(column.name for column in table.primary_key.columns)
        if primary_columns:
            indexes[(table.name, "PRIMARY")] = IndexShape(
                table=table.name, name="PRIMARY", columns=primary_columns, unique=True
            )
        for constraint in table.constraints:
            constraint_name = str(constraint.name or "")
            if isinstance(constraint, UniqueConstraint) and constraint_name:
                indexes[(table.name, constraint_name)] = IndexShape(
                    table=table.name,
                    name=constraint_name,
                    columns=tuple(column.name for column in constraint.columns),
                    unique=True,
                )
            elif isinstance(constraint, CheckConstraint) and constraint_name:
                checks[(table.name, constraint_name)] = CheckShape(
                    table=table.name,
                    name=constraint_name,
                    clause=normalize_clause(str(constraint.sqltext)),
                )
        for index in table.indexes:
            index_name = str(index.name or "")
            if index_name:
                indexes[(table.name, index_name)] = IndexShape(
                    table=table.name,
                    name=index_name,
                    columns=tuple(column.name for column in index.columns),
                    unique=bool(index.unique),
                )

    return SchemaShape(tables=tables, columns=columns, indexes=indexes, checks=checks)


def read_schema(engine: Engine) -> SchemaShape:
    tables: dict[str, TableShape] = {}
    columns: dict[tuple[str, str], ColumnShape] = {}
    indexes: dict[tuple[str, str], IndexShape] = {}
    checks: dict[tuple[str, str], CheckShape] = {}

    with engine.connect() as connection:
        table_rows = connection.execute(
            text(
                "SELECT table_name, engine, table_collation, table_comment "
                "FROM information_schema.tables WHERE table_schema = DATABASE() "
                "ORDER BY table_name"
            )
        ).all()
        for row in table_rows:
            name = str(row[0])
            if name == ALEMBIC_VERSION_TABLE:
                continue
            tables[name] = TableShape(
                name=name,
                engine=str(row[1] or ""),
                collation=str(row[2] or ""),
                comment=str(row[3] or ""),
            )

        column_rows = connection.execute(
            text(
                "SELECT table_name, column_name, column_type, is_nullable, column_default, "
                "extra, generation_expression, column_comment "
                "FROM information_schema.columns WHERE table_schema = DATABASE() "
                "ORDER BY table_name, ordinal_position"
            )
        ).all()
        for row in column_rows:
            table_name = str(row[0])
            column_name = str(row[1])
            if table_name == ALEMBIC_VERSION_TABLE:
                continue
            column_type = str(row[2])
            is_nullable = str(row[3])
            column_default = None if row[4] is None else str(row[4])
            extra = None if row[5] is None else str(row[5])
            generation_expression = None if row[6] is None else str(row[6])
            column_comment = None if row[7] is None else str(row[7])

            default, on_update = normalize_default(column_default)
            extra_lower = (extra or "").lower()
            if "on update" in extra_lower:
                _, extra_update = normalize_default(extra)
                on_update = extra_update
            columns[(table_name, column_name)] = ColumnShape(
                table=table_name,
                name=column_name,
                type=normalize_type(column_type),
                nullable=is_nullable == "YES",
                default=default,
                on_update=on_update,
                auto_increment="auto_increment" in extra_lower,
                computed=(
                    normalize_expression(generation_expression)
                    if generation_expression
                    else None
                ),
                comment=column_comment or "",
            )

        index_rows = connection.execute(
            text(
                "SELECT table_name, index_name, "
                "GROUP_CONCAT(column_name ORDER BY seq_in_index) AS column_list, non_unique "
                "FROM information_schema.statistics WHERE table_schema = DATABASE() "
                "GROUP BY table_name, index_name, non_unique ORDER BY table_name, index_name"
            )
        ).all()
        for row in index_rows:
            table_name = str(row[0])
            index_name = str(row[1])
            if table_name == ALEMBIC_VERSION_TABLE:
                continue
            column_list = str(row[2])
            non_unique = int(str(row[3]))
            indexes[(table_name, index_name)] = IndexShape(
                table=table_name,
                name=index_name,
                columns=tuple(column_list.split(",")),
                unique=non_unique == 0,
            )

        check_rows = connection.execute(
            text(
                "SELECT tc.table_name, cc.constraint_name, cc.check_clause "
                "FROM information_schema.check_constraints cc "
                "JOIN information_schema.table_constraints tc "
                "  ON tc.constraint_schema = cc.constraint_schema "
                " AND tc.constraint_name = cc.constraint_name "
                "WHERE tc.table_schema = DATABASE() ORDER BY tc.table_name, cc.constraint_name"
            )
        ).all()
        for row in check_rows:
            table_name = str(row[0])
            constraint_name = str(row[1])
            if table_name == ALEMBIC_VERSION_TABLE:
                continue
            checks[(table_name, constraint_name)] = CheckShape(
                table=table_name,
                name=constraint_name,
                clause=normalize_clause(str(row[2])),
            )

    return SchemaShape(tables=tables, columns=columns, indexes=indexes, checks=checks)


def diff_schemas(actual: SchemaShape, expected: SchemaShape) -> tuple[str, ...]:
    diffs: list[str] = []

    for name in sorted(set(expected.tables) - set(actual.tables)):
        diffs.append(f"缺少表 {name}")
    for name in sorted(set(actual.tables) - set(expected.tables)):
        diffs.append(f"多出表 {name}")
    for name in sorted(set(actual.tables) & set(expected.tables)):
        actual_table = actual.tables[name]
        expected_table = expected.tables[name]
        if (actual_table.engine, actual_table.collation) != (
            expected_table.engine,
            expected_table.collation,
        ):
            diffs.append(
                f"表 {name} 引擎/排序规则不一致："
                f"{actual_table.engine}/{actual_table.collation} != "
                f"{expected_table.engine}/{expected_table.collation}"
            )
        if actual_table.comment != expected_table.comment:
            diffs.append(f"表 {name} 注释不一致")

    for key in sorted(set(expected.columns) - set(actual.columns)):
        diffs.append(f"缺少列 {key[0]}.{key[1]}")
    for key in sorted(set(actual.columns) - set(expected.columns)):
        diffs.append(f"多出列 {key[0]}.{key[1]}")
    for key in sorted(set(actual.columns) & set(expected.columns)):
        actual_column = actual.columns[key]
        expected_column = expected.columns[key]
        for field in (
            "type",
            "nullable",
            "default",
            "on_update",
            "auto_increment",
            "computed",
            "comment",
        ):
            actual_value = getattr(actual_column, field)
            expected_value = getattr(expected_column, field)
            if actual_value != expected_value:
                diffs.append(
                    f"列 {key[0]}.{key[1]} 的 {field} 不一致："
                    f"{actual_value!r} != {expected_value!r}"
                )

    for key in sorted(set(expected.indexes) - set(actual.indexes)):
        diffs.append(f"缺少索引 {key[0]}.{key[1]}")
    for key in sorted(set(actual.indexes) - set(expected.indexes)):
        diffs.append(f"多出索引 {key[0]}.{key[1]}")
    for key in sorted(set(actual.indexes) & set(expected.indexes)):
        actual_index = actual.indexes[key]
        expected_index = expected.indexes[key]
        if (actual_index.columns, actual_index.unique) != (
            expected_index.columns,
            expected_index.unique,
        ):
            diffs.append(f"索引 {key[0]}.{key[1]} 列或唯一性不一致")

    for key in sorted(set(expected.checks) - set(actual.checks)):
        diffs.append(f"缺少 CHECK {key[0]}.{key[1]}")
    for key in sorted(set(actual.checks) - set(expected.checks)):
        diffs.append(f"多出 CHECK {key[0]}.{key[1]}")
    for key in sorted(set(actual.checks) & set(expected.checks)):
        actual_check = actual.checks[key]
        expected_check = expected.checks[key]
        if actual_check.clause != expected_check.clause:
            diffs.append(f"CHECK {key[0]}.{key[1]} 条件不一致")

    return tuple(diffs)


def diff_database_to_metadata(
    engine: Engine, metadata: MetaData = default_metadata
) -> tuple[str, ...]:
    return diff_schemas(read_schema(engine), metadata_schema(metadata))


def assert_structure_matches_metadata(
    engine: Engine, metadata: MetaData = default_metadata
) -> None:
    diffs = diff_database_to_metadata(engine, metadata)
    if diffs:
        raise BaselineMismatch(diffs)


def stamp_baseline(url: URL, revision: str = "head", metadata: MetaData = default_metadata) -> None:
    """结构一致时才登记 Alembic 基线；不一致不写任何版本行。"""
    engine = create_database_engine(url, pool_pre_ping=True)
    try:
        assert_structure_matches_metadata(engine, metadata)
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
        command.stamp(config, revision)
    finally:
        engine.dispose()
