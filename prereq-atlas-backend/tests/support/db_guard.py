"""测试库解析与隔离守卫。

规则：
- 集成测试必须显式设置 ``TEST_DATABASE_URL``；未设置时失败，不静默跳过。
- 测试库名必须以 ``_test`` 结尾且不得等于开发库名。
- 守卫在建立任何数据库连接之前完成校验，误配时数据库写入为 0。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy.engine import URL, make_url

from app.infra.config import get_settings

TEST_DATABASE_ENV = "TEST_DATABASE_URL"

SYSTEM_DATABASES = frozenset(
    {"mysql", "information_schema", "performance_schema", "sys", "prereq_atlas"}
)


class DatabaseGuardError(RuntimeError):
    """测试库未配置或不隔离；集成测试必须显式失败。"""


def is_test_database_name(name: str | None) -> bool:
    return bool(name) and (name == "prereq_atlas_test" or name.endswith("_test"))


def resolve_test_database_url(
    environ: Mapping[str, str] | None = None,
    *,
    development_url: str | None = None,
) -> URL:
    """解析并校验测试库 URL；不建立连接。"""
    env = os.environ if environ is None else environ
    raw = (env.get(TEST_DATABASE_ENV) or "").strip()
    if not raw:
        raise DatabaseGuardError(
            f"未设置 {TEST_DATABASE_ENV}；集成测试拒绝在未配置独立测试库时运行"
        )
    if "change-me" in raw:
        raise DatabaseGuardError(f"{TEST_DATABASE_ENV} 仍是占位值，拒绝运行集成测试")

    url = make_url(raw)
    if url.get_backend_name() != "mysql":
        raise DatabaseGuardError("测试库必须是 MySQL")
    name = url.database
    if not name:
        raise DatabaseGuardError("测试库 URL 必须包含库名")
    if name in SYSTEM_DATABASES:
        raise DatabaseGuardError(f"测试库名 {name!r} 是系统库或开发库，拒绝运行")
    if not is_test_database_name(name):
        raise DatabaseGuardError(f"测试库名 {name!r} 必须以 _test 结尾")

    dev_raw = development_url if development_url is not None else get_settings().database_url
    dev_name = make_url(dev_raw).database
    if name == dev_name:
        raise DatabaseGuardError(f"测试库不能与开发库同名：{name!r}")
    return url

