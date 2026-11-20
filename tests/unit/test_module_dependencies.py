"""Executable architectural rules for the modular monolith."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src"


def imports():
    modules = {
        ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__"): path
        for path in ROOT.rglob("*.py")
    }
    edges = {name: set() for name in modules}
    for name, path in modules.items():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
                    suffix = (node.module or "").split(".") if node.module else []
                    target = ".".join(parts[: len(parts) - node.level + 1] + suffix)
                else:
                    target = node.module or ""
                edges[name].add(target)
            elif isinstance(node, ast.Import):
                edges[name].update(item.name for item in node.names)
    return modules, edges


def test_business_modules_do_not_depend_on_http_or_api():
    modules, edges = imports()
    for name in modules:
        if name.startswith("vn_stream.api") or name == "vn_stream.__main__":
            continue
        forbidden = [
            m for m in edges[name] if m.startswith(("vn_stream.api", "fastapi", "starlette"))
        ]
        assert not forbidden, (name, forbidden)


def test_explicit_python_module_dependencies_are_acyclic():
    modules, edges = imports()
    visited, active = set(), []

    def visit(name):
        assert name not in active, " -> ".join([*active, name])
        if name in visited:
            return
        visited.add(name)
        active.append(name)
        for target in sorted(edges[name] & modules.keys()):
            visit(target)
        active.pop()

    for name in modules:
        visit(name)


def test_storage_implementation_does_not_depend_on_business_modules():
    _, edges = imports()
    for name, targets in edges.items():
        if name.startswith("vn_stream.storage."):
            assert all(
                not target.startswith("vn_stream.")
                or target.startswith(("vn_stream.storage", "vn_stream.i18n"))
                for target in targets
            ), (name, targets)


def test_public_module_imports_do_not_depend_on_previous_import_order():
    import os
    import subprocess
    import sys

    for module in ("vn_stream.runtimes.catalog", "vn_stream.engines.registry", "vn_stream.api.app"):
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
