from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _imports_under(root: Path) -> list[tuple[Path, str]]:
    imports: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend((path, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append((path, node.module))
    return imports


def test_deeptutor_core_does_not_import_exam_mem() -> None:
    violations = [
        (str(path.relative_to(PROJECT_ROOT)), module)
        for path, module in _imports_under(PROJECT_ROOT / "deeptutor")
        if module == "exam_mem" or module.startswith("exam_mem.")
    ]

    assert violations == []


def test_exam_mem_depends_only_on_the_neutral_host_plugin_api() -> None:
    violations = [
        (str(path.relative_to(PROJECT_ROOT)), module)
        for path, module in _imports_under(PROJECT_ROOT / "exam_mem")
        if (module == "deeptutor" or module.startswith("deeptutor."))
        and not module.startswith("deeptutor.plugins")
    ]

    assert violations == []
