#!/usr/bin/env bash
# Starts dev-db, dev-api, dev-worker and dev-web together; Ctrl+C stops them all.
#
# Not just `mise run dev-db ::: dev-api ::: dev-worker ::: dev-web`: a terminal Ctrl+C under mise's
# own parallelism reliably stops the API and Vite but not the Postgres container, so the database
# gets an explicit `compose down` here.
set -uo pipefail
if [ "$#" -gt 0 ]; then
  echo "usage: ${BASH_SOURCE[0]##*/}" >&2
  exit 2
fi
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mise run dev-db &
mise run dev-api &
mise run dev-worker &
mise run dev-web &

cleanup() {
  trap - INT TERM EXIT
  scripts/compose down
  kill -INT $(jobs -p) 2>/dev/null
  wait
}
trap cleanup INT TERM EXIT

wait -n
cleanup
