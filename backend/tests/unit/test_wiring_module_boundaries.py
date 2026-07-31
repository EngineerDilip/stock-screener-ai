"""Architecture boundaries for dependency-injection wiring."""

import ast
from pathlib import Path


def test_use_case_factories_do_not_import_bootstrap_facade() -> None:
    path = Path(__file__).parents[2] / "app/wiring/use_case_factories.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "app.wiring.bootstrap" not in imported_modules


def test_runtime_context_does_not_erase_runtime_services_to_any() -> None:
    path = Path(__file__).parents[2] / "app/wiring/runtime_context.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    any_references = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "Any"
    ]

    assert any_references == []
