#!/usr/bin/env bash
# Switches imap-thingy between the pinned source of backend/pyproject.toml and a local editable
# checkout, for work that spans repositories. (mailroom-core is a workspace member, always editable.) Nothing tracked changes: the choice
# lives in backend/.deps-local (gitignored), which `mise run setup` reads on every sync.
#
#   local   remember the checkout and install it editable; the default is the main checkout's
#           sibling ../imap-thingy, overridden by IMAP_THINGY_PATH (a worktree of it, say)
#   pinned  forget it and sync back to the lock
#   sync    `uv sync`; with a local checkout chosen, syncs everything else and leaves it installed
#           (run by `mise run setup` and every task that depends on it)
#   apply   (re)install the remembered checkout editable, if any
set -euo pipefail

self="$(realpath "${BASH_SOURCE[0]}")"
cd "$(dirname "$self")/../backend"
state=.deps-local

case "${1:-}" in
  local)
    # The main checkout, not this one: worktrees live under .claude/worktrees/.
    family="$(cd "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.." && pwd)"
    # Relative overrides are relative to the repository root, as in .env.
    imap="$(cd .. && realpath "${IMAP_THINGY_PATH:-$family/imap-thingy}")"
    [[ -f "$imap/pyproject.toml" ]] || { echo "no pyproject.toml in $imap" >&2; exit 1; }
    printf '%s\n' "$imap" >"$state"
    "$self" sync
    ;;
  pinned)
    rm -f "$state"
    # A plain sync takes the editable installs for the locked ones.
    uv sync --all-groups --reinstall-package imap-thingy
    echo "imap-thingy is back on the pinned source"
    ;;
  sync)
    if [[ ! -f "$state" ]]; then
      exec uv sync --all-groups
    fi
    # Never swapped for the pinned ones, not even for a moment: tasks run setup concurrently
    # (`mise run dev`), and a process importing during that window would load the wrong version.
    uv sync --all-groups --inexact --no-install-package imap-thingy
    "$self" apply
    ;;
  apply)
    [[ -f "$state" ]] || exit 0
    mapfile -t paths <"$state"
    # The project's environment, not UV_PYTHON's interpreter (mise.toml points it at mise's own).
    # `compat`: a plain path in a .pth, which mypy follows (setuptools' default import hook it
    # does not).
    uv pip install -q --python .venv --config-settings editable_mode=compat "${paths[@]/#/--editable=}"
    echo "local editable: ${paths[*]} (\`mise run deps-pinned\` to switch back)"
    ;;
  *)
    echo "usage: ${BASH_SOURCE[0]##*/} local|pinned|sync|apply" >&2
    exit 2
    ;;
esac
