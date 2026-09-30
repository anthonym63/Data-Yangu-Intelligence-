"""Architectural guards the rest of the suite can't express."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).parent.parent / "src" / "backend"
SHARED = Path(__file__).parent.parent / "src" / "contracts.py"

#: Anything that can open a socket or speak HTTP. The backend must never
#: import one - the privacy model depends on the data path being local.
FORBIDDEN_MODULES = {
    "anthropic", "openai", "httpx", "httpx2", "requests", "urllib", "urllib3",
    "aiohttp", "socket", "http", "ftplib", "smtplib", "websocket", "websockets", "fastmcp",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("path", sorted(BACKEND.glob("*.py")), ids=lambda p: p.name)
def test_backend_imports_no_network_capable_module(path):
    assert not (_imports(path) & FORBIDDEN_MODULES), f"{path.name} imports a network-capable module"


def test_backend_does_not_import_pandas():
    for path in BACKEND.glob("*.py"):
        assert "pandas" not in _imports(path), f"{path.name} imports pandas (CLAUDE.md 2)"


def test_contracts_module_is_dependency_free():
    assert _imports(SHARED) <= {"typing"}


def test_backend_never_reaches_into_frontend():
    for path in BACKEND.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "src.frontend" not in src and "streamlit" not in _imports(path), path.name
