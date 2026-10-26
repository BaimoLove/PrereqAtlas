"""模块导入图的静态检查。

规则来自 ``plan.md`` 第 3.2/4.4 节：

1. 跨模块只能导入对方的 ``public``，不得导入 ``models/repository/service/router/schema``；
2. ``planning`` 与 ``graph_runtime`` 是纯计算模块，不得导入数据库、HTTP、文件访问或业务模块；
3. 反向业务依赖（如 catalog -> graph_admin）直接拒绝。

检查只覆盖导入图；运行时的 I/O 次数由 T13/T18 的行为测试负责。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

INTERNAL_MODULE_NAMES = frozenset(
    {
        "models",
        "repository",
        "repositories",
        "service",
        "services",
        "router",
        "routers",
        "schema",
        "schemas",
        "engine",
        "builder",
        "cache",
    }
)

PURE_MODULE_PACKAGES = frozenset({"planning", "graph_runtime"})

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "sqlalchemy",
        "fastapi",
        "starlette",
        "httpx",
        "requests",
        "pymysql",
        "alembic",
        "jwt",
        "pwdlib",
        "pathlib",
        "tempfile",
        "shutil",
        "io",
    }
)

# 纯计算模块也不得访问应用基础设施。
PURE_FORBIDDEN_APP_PREFIXES = ("app.infra", "app.api", "app.main")

# 明确禁止的反向依赖（即使走 public 也不行）。
FORBIDDEN_EDGES = {
    ("catalog", "graph_admin"): "catalog 不得反向依赖 graph_admin",
    ("catalog", "plans"): "catalog 不得依赖 plans",
    ("mastery", "plans"): "mastery 不得依赖 plans",
    ("ingestion", "graph_admin"): "ingestion 不得反向依赖 graph_admin",
}


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    imported: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}: {self.imported}"


@dataclass(frozen=True, slots=True)
class _ImportRef:
    module: str
    names: tuple[str, ...]
    line: int


def _module_parts(module: str) -> list[str]:
    return module.split(".")


def _current_package(current_module: str) -> str | None:
    parts = _module_parts(current_module)
    if len(parts) >= 3 and parts[0] == "app" and parts[1] == "modules":
        return parts[2]
    return None


def _resolve_relative(current_module: str, level: int, module: str | None) -> str:
    parts = _module_parts(current_module)
    base = parts[:-1]
    if level > 1:
        base = base[: -(level - 1)]
    if module:
        base = [*base, *module.split(".")]
    return ".".join(base)


def _iter_imports(tree: ast.AST, current_module: str) -> list[_ImportRef]:
    refs: list[_ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs.append(_ImportRef(module=alias.name, names=(), line=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                module = _resolve_relative(current_module, node.level, node.module)
            else:
                module = node.module or ""
            refs.append(
                _ImportRef(
                    module=module,
                    names=tuple(alias.name for alias in node.names),
                    line=node.lineno,
                )
            )
    return refs


def _check_ref(
    ref: _ImportRef, current_module: str, path: Path
) -> Violation | None:
    imported = ref.module
    if not imported:
        return None
    display = f"{imported}:{','.join(ref.names)}" if ref.names else imported

    current_pkg = _current_package(current_module)
    is_pure = current_pkg in PURE_MODULE_PACKAGES
    root = imported.split(".", 1)[0]

    if is_pure:
        if root in FORBIDDEN_IMPORT_ROOTS:
            return Violation(path, ref.line, display, "纯计算模块不得依赖基础设施")
        if imported.startswith(PURE_FORBIDDEN_APP_PREFIXES):
            return Violation(path, ref.line, display, "纯计算模块不得依赖应用基础设施")
        if imported.startswith("app.modules."):
            target = _module_parts(imported)
            if len(target) >= 3 and target[2] != current_pkg:
                return Violation(path, ref.line, display, "纯计算模块不得依赖其他业务模块")

    if not imported.startswith("app.modules."):
        return None

    parts = _module_parts(imported)
    if len(parts) < 3:
        return None
    target_pkg = parts[2]
    if target_pkg == current_pkg:
        return None

    reason = FORBIDDEN_EDGES.get((current_pkg or "", target_pkg))
    if reason is not None:
        return Violation(path, ref.line, display, f"依赖方向不允许：{reason}")

    target_leaf = parts[3] if len(parts) >= 4 else None
    if target_leaf in INTERNAL_MODULE_NAMES:
        return Violation(
            path,
            ref.line,
            display,
            f"跨模块内部导入不允许，{current_pkg} 只能使用 {target_pkg}.public",
        )

    if target_leaf == "public":
        return None

    if ref.names:
        if set(ref.names) == {"public"}:
            return None
        return Violation(
            path,
            ref.line,
            display,
            f"跨模块导入必须指向 {target_pkg}.public，不能导入 {', '.join(ref.names)}",
        )

    # 只导入包本身不暴露内部结构，允许。
    return None


def scan_text(source: str, *, current_module: str, path: Path | None = None) -> list[Violation]:
    """扫描一段源码；供真实树和故意非法样例共用。"""
    tree = ast.parse(source)
    target_path = path or Path("<memory>")
    violations: list[Violation] = []
    for ref in _iter_imports(tree, current_module):
        violation = _check_ref(ref, current_module, target_path)
        if violation is not None:
            violations.append(violation)
    return violations


def _module_name_for(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    return ".".join(relative.parts)


def scan_tree(root: Path) -> list[Violation]:
    """扫描 ``root/app/modules`` 下全部 Python 文件的导入图。"""
    modules_root = root / "app" / "modules"
    base = modules_root if modules_root.is_dir() else root
    violations: list[Violation] = []
    for path in sorted(base.rglob("*.py")):
        current_module = _module_name_for(path, root)
        source = path.read_text(encoding="utf-8")
        violations.extend(scan_text(source, current_module=current_module, path=path))
    return violations
