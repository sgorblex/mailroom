"""Runtime API build identity (public ``/api/v1/version``)."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version


def api_distribution_version() -> str:
    try:
        return version("mailroom")
    except PackageNotFoundError:
        return "0.0.0"


def api_git_sha() -> str:
    return os.environ.get("MAILROOM_GIT_SHA", "").strip()


def api_git_branch() -> str:
    return os.environ.get("MAILROOM_GIT_BRANCH", "").strip()


def api_deploy_env() -> str:
    raw = os.environ.get("MAILROOM_DEPLOY_ENV", "").strip().lower()
    if raw in ("production", "staging", "development"):
        return raw
    if raw == "":
        return "production"
    return "unknown"
