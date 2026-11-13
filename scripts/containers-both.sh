#!/usr/bin/env bash
# Builds mailroom-api then mailroom-web. With MAILROOM_REGISTRY set, nothing is pushed until both
# have built, so a failed build never leaves :latest in the registry split across two releases.
# Each image's own tags (:<version>, :staging-<branch>-<sha>) are pushed first, the moving ones
# (:latest, :staging) last.
set -euo pipefail

_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$_repo_root"

release="${1:-production}"

# container-build.sh appends `fixed|moving <engine> <ref>` lines here instead of pushing; with
# MAILROOM_REGISTRY unset it builds local tags only and the list stays empty.
push_list="$(mktemp)"
trap 'rm -f "$push_list"' EXIT
MAILROOM_PUSH_LIST="$push_list" "$_repo_root/scripts/container-build.sh" api "$release"
MAILROOM_PUSH_LIST="$push_list" "$_repo_root/scripts/container-build.sh" web "$release"

for kind in fixed moving; do
  while read -r line_kind engine ref; do
    if [[ "$line_kind" == "$kind" ]]; then
      "$engine" push "$ref" </dev/null
    fi
  done <"$push_list"
done
