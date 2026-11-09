import tomllib
from importlib.metadata import version
from pathlib import Path

import mailroom_core

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def test_installed_in_the_workspace() -> None:
    assert mailroom_core.__name__ == "mailroom_core"
    declared = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    assert version("mailroom-core") == declared
