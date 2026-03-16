"""Path-only folder representation (segment list, no account).

An empty path ("" or []) denotes the server root. The inbox is the single
segment 'INBOX'.
"""

from __future__ import annotations


class Path:
    """Path as segments; use / to append a segment."""

    def __init__(self, path: str | list[str]) -> None:
        """Initialize path from a path string or list of segments."""
        if isinstance(path, str):
            segments = [path] if path else []
        else:
            segments = list(path)
        self.segments = segments

    def as_string(self, delimiter: str = "/") -> str:
        """Return the path as a string by joining segments with delimiter."""
        return delimiter.join(self.segments)

    def __truediv__(self, path: str) -> Path:
        return Path(self.segments + [path])

    def __repr__(self) -> str:
        return f"Path({self.segments!r})"
