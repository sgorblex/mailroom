"""Config errors, worded as `src/config/load.ts` words them (zod's `prettifyError`)."""

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_core import PydanticCustomError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import ValidationError

type _IssuePath = tuple[str | int, ...]


class ConfigError(Exception):
    """A config or settings document that cannot be used; the message says why, never a secret."""


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    """One problem in a document, at a path like `account[0].smtp.tls`."""

    path: _IssuePath
    message: str


def config_problem(message: str) -> PydanticCustomError:
    """A validation error worded exactly `message` (a ValueError would get a prefix)."""
    return PydanticCustomError("config", "{message}", {"message": message})


def validation_issues(error: ValidationError, prefix: _IssuePath = ()) -> list[ConfigIssue]:
    """Pydantic's errors as issues, the unknown keys of one table in one issue as zod has them."""
    details = error.errors(include_url=False, include_input=False)
    paths = [prefix + tuple(detail["loc"]) for detail in details]
    found: list[tuple[_IssuePath, str | list[str]]] = []
    unknown: dict[_IssuePath, list[str]] = {}
    for detail, path in zip(details, paths, strict=True):
        if detail["type"] == "too_short":
            # Pydantic counts the items that passed: an array whose items failed is also short.
            if any(other[: len(path)] == path and other != path for other in paths):
                continue
            minimum = (detail.get("ctx") or {}).get("min_length")
            found.append((path, f"Too small: expected array to have >={minimum} items"))
        elif detail["type"] == "string_too_short":
            minimum = (detail.get("ctx") or {}).get("min_length")
            found.append((path, f"Too small: expected string to have >={minimum} characters"))
        elif detail["type"] != "extra_forbidden":
            found.append((path, _MESSAGES.get(detail["type"], detail["msg"])))
        elif path[:-1] in unknown:
            unknown[path[:-1]].append(str(path[-1]))
        else:
            unknown[path[:-1]] = [str(path[-1])]
            found.append((path[:-1], unknown[path[:-1]]))
    return [
        ConfigIssue(path, message if isinstance(message, str) else _unrecognized(message))
        for path, message in found
    ]


def issues_text(issues: Iterable[ConfigIssue]) -> str:
    """One `✖ message` line per issue, then `  → at path` unless it is about the whole document."""
    lines: list[str] = []
    for issue in sorted(issues, key=lambda issue: len(issue.path)):
        lines.append(f"✖ {issue.message}")
        if issue.path:
            lines.append(f"  → at {_dot_path(issue.path)}")
    return "\n".join(lines)


# Pydantic's wording names Python types; the documents are TOML or JSON.
_MESSAGES = {
    "tuple_type": "Input should be an array",
    "model_type": "Input should be an object",
    "model_attributes_type": "Input should be an object",
    "dict_type": "Input should be an object",
}


def _unrecognized(keys: list[str]) -> str:
    quoted = ", ".join(json.dumps(key, ensure_ascii=False) for key in keys)
    return f"Unrecognized key{'s' if len(keys) > 1 else ''}: {quoted}"


_PLAIN_KEY = re.compile(r"[\w$]+", re.ASCII)


def _dot_path(path: _IssuePath) -> str:
    out = ""
    for segment in path:
        if isinstance(segment, int):
            out += f"[{segment}]"
        elif not _PLAIN_KEY.fullmatch(segment):
            out += f"[{json.dumps(segment, ensure_ascii=False)}]"
        else:
            out += f".{segment}" if out else segment
    return out
