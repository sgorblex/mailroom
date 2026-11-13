#!/usr/bin/env bash
# Runs a test command (the arguments, from the repository root) against a throwaway Dovecot (IMAP)
# and two Mailpits (SMTP sinks, implicit TLS and STARTTLS) in podman. Each run starts its own
# containers on free ports and removes them afterwards, so runs from several worktrees never share
# or stop each other's servers. Adapted from mailroom-mcp's test-integration.sh.
# `mise run test-core-mail` passes mailroom-core's pytest, `mise run test-backend-mail` the app's.
# Nothing signs these servers' certificates, so MAILROOM_MAIL_CA_FILE (the dev CA, from mise) is
# cleared: the app's tests then trust them as core's do (backend/tests/support/mail.py).
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <test command> [args...]" >&2
  exit 2
fi

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# shellcheck source=images.sh
source scripts/images.sh
# shellcheck source=lib.sh
source scripts/lib.sh

suffix=$$
dovecot=mailroom-app-test-dovecot-$suffix
mailpit=mailroom-app-test-mailpit-$suffix
starttls=mailroom-app-test-mailpit-starttls-$suffix

started=()
cleanup() { for name in "${started[@]}"; do podman stop -t 1 "$name" >/dev/null 2>&1 || true; done; }
trap cleanup EXIT

# The host port podman picked for container $1's port $2.
host_port() { local mapping; mapping=$(podman port "$1" "$2/tcp"); echo "${mapping##*:}"; }

podman run -d --rm --name "$dovecot" -e USER_PASSWORD=pass \
  -v "$PWD/dev/dovecot-test.conf:/etc/dovecot/conf.d/99-test.conf:ro" \
  -p 127.0.0.1::31993 "$dovecot_image" >/dev/null
started+=("$dovecot")
# Implicit TLS with a generated self-signed certificate, as production's port 465; any login.
podman run -d --rm --name "$mailpit" \
  -e MP_SMTP_AUTH_ACCEPT_ANY=true -e MP_SMTP_REQUIRE_TLS=true \
  -e MP_SMTP_TLS_CERT=sans:localhost -e MP_SMTP_TLS_KEY=sans:localhost \
  -p 127.0.0.1::1025 -p 127.0.0.1::8025 "$mailpit_image" >/dev/null
started+=("$mailpit")
# STARTTLS on a plain port, as production's port 587.
podman run -d --rm --name "$starttls" \
  -e MP_SMTP_AUTH_ACCEPT_ANY=true -e MP_SMTP_REQUIRE_STARTTLS=true \
  -e MP_SMTP_TLS_CERT=sans:localhost -e MP_SMTP_TLS_KEY=sans:localhost \
  -p 127.0.0.1::1025 -p 127.0.0.1::8025 "$mailpit_image" >/dev/null
started+=("$starttls")

port=$(host_port "$dovecot" 31993)
smtp_port=$(host_port "$mailpit" 1025)
api_port=$(host_port "$mailpit" 8025)
starttls_port=$(host_port "$starttls" 1025)
starttls_api_port=$(host_port "$starttls" 8025)

await_greeting Dovecot "$port" '^\* OK'
await_greeting Mailpit "$smtp_port" '^220'
await_plain_greeting 'Mailpit (STARTTLS)' "$starttls_port" '^220'

MAILROOM_MAIL_CA_FILE= IMAP_TEST_HOST=127.0.0.1 IMAP_TEST_PORT="$port" \
  SMTP_TEST_HOST=127.0.0.1 SMTP_TEST_PORT="$smtp_port" MAILPIT_TEST_API="http://127.0.0.1:$api_port" \
  SMTP_TEST_STARTTLS_HOST=127.0.0.1 SMTP_TEST_STARTTLS_PORT="$starttls_port" \
  MAILPIT_TEST_STARTTLS_API="http://127.0.0.1:$starttls_api_port" \
  "$@"
