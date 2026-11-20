"""The top-level domains of the root zone (`tlds.txt`, kept by hand): a dotted word names a host
only when it ends in one."""

from importlib.resources import files
from typing import Final

TLDS: Final = frozenset(
    name
    for line in files(__package__).joinpath("tlds.txt").read_text(encoding="ascii").splitlines()
    if not line.startswith("#")
    for name in line.split()
)
