"""Import boundaries inside mailroom-core (docs/core.md — *Public API*)."""

from __future__ import annotations

import ast
from pathlib import Path

import mailroom_core

_PACKAGE = Path(mailroom_core.__file__).resolve().parent


def _module_name(path: Path) -> str:
    parts = path.relative_to(_PACKAGE.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    """Every module ``path`` imports, relative imports resolved, with its line."""
    name = _module_name(path)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    modules: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                parent = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                base = f"{parent}.{node.module}" if node.module else parent
            modules.append((node.lineno, base))
            # ``from mailroom_core import filters`` names the subpackage as an imported name.
            modules.extend((node.lineno, f"{base}.{alias.name}") for alias in node.names)
    return modules


def _imports(directory: Path, banned: str) -> list[str]:
    """Each line under ``directory`` that imports ``banned`` or one of its modules, once."""
    lines: dict[str, str] = {}
    for path in sorted(directory.rglob("*.py")):
        for line, module in _imported_modules(path):
            if module == banned or module.startswith(f"{banned}."):
                lines.setdefault(f"{path.relative_to(_PACKAGE.parent)}:{line}", module)
    return [f"{where}: {module}" for where, module in lines.items()]


def test_mail_never_imports_filters() -> None:
    """``filters`` may build on ``mail``, never the other way round."""
    assert _imports(_PACKAGE / "mail", "mailroom_core.filters") == []


def test_core_never_imports_the_app() -> None:
    """Core knows nothing of the app it sits under."""
    assert _imports(_PACKAGE, "mailroom") == []
