from importlib.metadata import version

import mailroom_core


def test_installed_in_the_workspace() -> None:
    assert mailroom_core.__name__ == "mailroom_core"
    assert version("mailroom-core") == "0.0.0"
