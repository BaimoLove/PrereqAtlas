"""架构检查：跨模块只能走 public，纯计算模块不得依赖基础设施。

T02 只检查模块导入图；运行时的数据库/HTTP/文件访问由 T13/T18 的行为测试补充。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from architecture.import_policy import INTERNAL_MODULE_NAMES, scan_text, scan_tree

SRC = Path(__file__).resolve().parents[2] / "src"


def test_real_source_tree_has_no_violations() -> None:
    violations = scan_tree(SRC)
    assert violations == [], "\n".join(str(item) for item in violations)


def test_detects_cross_module_internal_import() -> None:
    source = "from app.modules.catalog.models import Concept\n"
    violations = scan_text(source, current_module="app.modules.identity.models")
    assert len(violations) == 1
    assert "catalog.models" in violations[0].imported
    assert "跨模块" in violations[0].reason


def test_detects_cross_module_internal_import_via_from_package() -> None:
    source = "from app.modules.catalog import models\n"
    violations = scan_text(source, current_module="app.modules.identity.models")
    assert len(violations) == 1
    assert "models" in violations[0].imported


def test_detects_plain_import_of_another_module_internal() -> None:
    source = "import app.modules.catalog.models\n"
    violations = scan_text(source, current_module="app.modules.identity.models")
    assert len(violations) == 1


def test_allows_cross_module_public_import() -> None:
    source = "from app.modules.catalog.public import get_concept\n"
    assert scan_text(source, current_module="app.modules.identity.public") == []


def test_detects_infrastructure_import_in_pure_module() -> None:
    for source in (
        "from sqlalchemy import text\n",
        "import httpx\n",
        "from app.infra.db import session_scope\n",
        "from fastapi import APIRouter\n",
    ):
        violations = scan_text(source, current_module="app.modules.planning.engine")
        assert len(violations) == 1, source
        assert "纯计算" in violations[0].reason


def test_detects_reverse_dependency_catalog_to_graph_admin() -> None:
    source = "from app.modules.graph_admin.public import create_draft\n"
    violations = scan_text(source, current_module="app.modules.catalog.service")
    assert len(violations) == 1
    assert "依赖方向" in violations[0].reason


def test_internal_module_names_are_documented() -> None:
    assert {"models", "repository", "service", "router", "schema"} <= INTERNAL_MODULE_NAMES


def test_scan_tree_reports_file_and_line() -> None:
    root = Path(__file__).resolve().parent / "_scan_tmp"
    shutil.rmtree(root, ignore_errors=True)
    module_dir = root / "app" / "modules" / "identity"
    module_dir.mkdir(parents=True)
    (module_dir / "models.py").write_text(
        "from app.modules.catalog.models import Concept\n", encoding="utf-8"
    )
    try:
        violations = scan_tree(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    assert len(violations) == 1
    assert violations[0].line == 1
    assert violations[0].path.name == "models.py"


@pytest.mark.parametrize(
    "source",
    [
        "from app.common.contracts import Actor\n",
        "from app.infra.db import Base\n",
        "from app.modules.identity.models import AppUser\n",
        "from .models import AppUser\n",
    ],
)
def test_allowed_imports_do_not_fail(source: str) -> None:
    assert scan_text(source, current_module="app.modules.identity.service") == []
