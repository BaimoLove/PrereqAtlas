"""Alembic 运行环境：连接串优先取显式配置，其次应用配置；元数据来自全部实体。"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.infra.config import get_settings
from app.infra.db import configure_database_timezone
from app.infra.metadata import metadata as target_metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 测试与回溯场景可在 Config 中显式指定 URL；命令行默认使用应用配置。
_configured_url = (config.get_main_option("sqlalchemy.url") or "").strip()
if not _configured_url:
    _configured_url = get_settings().database_url
config.set_main_option("sqlalchemy.url", _configured_url)


def _configure_context(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不建立数据库连接。"""
    _configure_context(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库并执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    configure_database_timezone(connectable)

    with connectable.connect() as connection:
        _configure_context(connection=connection)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
