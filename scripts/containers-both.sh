#!/usr/bin/env bash
set -euo pipefail

_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$_repo_root"

release="${1:-production}"
"$_repo_root/scripts/container-build.sh" api "$release"
"$_repo_root/scripts/container-build.sh" web "$release"
