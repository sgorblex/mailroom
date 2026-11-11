from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest

import mailroom_core
from mailroom_core.testing import NetworkAccessError

_BACKEND = Path(__file__).resolve().parents[1]
_CORE = Path(mailroom_core.__file__).resolve().parent
_APP_DIRS = ("mailroom", "alembic", "tests")


def test_core_plugin_refuses_remote_hosts() -> None:
    """mailroom-core's pytest plugin is loaded: a test cannot reach a host off this machine."""
    with socket.socket() as sock:
        sock.settimeout(1)
        with pytest.raises(NetworkAccessError):
            sock.connect(("192.0.2.1", 993))


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    modules: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append((node.lineno, node.module))
            # ``from mailroom_core.mail import audit`` imports a module by name.
            modules.extend(
                (node.lineno, f"{node.module}.{alias.name}")
                for alias in node.names
                if _is_core_module(f"{node.module}.{alias.name}")
            )
    return modules


def _is_core_module(name: str) -> bool:
    if not name.startswith("mailroom_core."):
        return False
    path = _CORE.joinpath(*name.split(".")[1:])
    return path.with_suffix(".py").is_file() or path.is_dir()


def test_app_imports_core_through_its_facades() -> None:
    """The app and its tests import mailroom-core's subpackages, never one of their modules."""
    below_a_facade = [
        f"{path.relative_to(_BACKEND)}:{line}: {module}"
        for directory in _APP_DIRS
        for path in sorted((_BACKEND / directory).rglob("*.py"))
        for line, module in _imported_modules(ast.parse(path.read_text(), str(path)))
        if module.startswith("mailroom_core.") and module.count(".") > 1
    ]
    assert below_a_facade == []
