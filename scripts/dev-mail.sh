#!/usr/bin/env bash
# The dev environment's mail backends: a Dovecot (IMAPS 127.0.0.1:33993) and a Mailpit (SMTPS
# 127.0.0.1:33025, web UI http://localhost:8026), kept between runs. Adapted from mailroom-mcp's
# copy, on other ports and container names so both dev environments can run at once. Both serve a
# certificate from a local dev CA (dev/.state/certs/ca.pem): TLS stays verified, as in production.
# Seeding sample mail comes later, from mailroom_core.testing.
#
#   up     create the CA once, start (or create) both containers
#   down   stop both containers; the mail stays
#   reset  remove containers and mail, then up
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=images.sh
source scripts/images.sh
# shellcheck source=lib.sh
source scripts/lib.sh

state="$PWD/dev/.state"
certs="$state/certs"
dovecot=mailroom-app-dev-dovecot
mailpit=mailroom-app-dev-mailpit
mail_volume=mailroom-app-dev-mail
mailpit_volume=mailroom-app-dev-mailpit

make_certs() {
  [[ -f "$certs/tls.crt" ]] && return
  mkdir -p "$certs"
  # Explicit keyUsage on both: Python 3.14's strict X.509 verification rejects a CA without it.
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
    -subj "/CN=mailroom app dev CA" -keyout "$certs/ca.key" -out "$certs/ca.pem" \
    -addext 'basicConstraints=critical,CA:true' -addext 'keyUsage=critical,keyCertSign,cRLSign' 2>/dev/null
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=localhost" \
    -keyout "$certs/tls.key" -out "$certs/tls.csr" 2>/dev/null
  openssl x509 -req -in "$certs/tls.csr" -CA "$certs/ca.pem" -CAkey "$certs/ca.key" \
    -CAcreateserial -days 3650 -out "$certs/tls.crt" \
    -extfile <(printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth') 2>/dev/null
  # Read by the services' own users inside the containers; it guards nothing outside them.
  chmod 644 "$certs/tls.key"
}

# Starts the container, creating it from the remaining arguments when it does not exist or was
# created from different ones (a new image pin, another password). The mail lives in named
# volumes, so recreating loses nothing.
start_or_create() {
  local name="$1"
  shift
  local key
  key=$(printf '%s\n' "$@" | sha256sum | cut -c1-16)
  if podman container exists "$name" &&
    [[ "$(podman container inspect -f '{{index .Config.Labels "mailroom.dev.key"}}' "$name")" != "$key" ]]; then
    podman rm -f "$name" >/dev/null
  fi
  if podman container exists "$name"; then
    podman start "$name" >/dev/null
  else
    podman run -d --name "$name" --label "mailroom.dev.key=$key" "$@" >/dev/null
  fi
}

up() {
  make_certs
  start_or_create "$dovecot" -e USER_PASSWORD="${DEV_MAIL_PASSWORD:-pass}" \
    -v "$PWD/dev/dovecot.conf:/etc/dovecot/conf.d/99-dev.conf:ro" \
    -v "$certs/tls.crt:/etc/dovecot/ssl/tls.crt:ro" -v "$certs/tls.key:/etc/dovecot/ssl/tls.key:ro" \
    -v "$mail_volume:/srv/vmail" -p 127.0.0.1:33993:31993 "$dovecot_image"
  start_or_create "$mailpit" -e MP_SMTP_AUTH_ACCEPT_ANY=true -e MP_SMTP_REQUIRE_TLS=true \
    -e MP_SMTP_TLS_CERT=/certs/tls.crt -e MP_SMTP_TLS_KEY=/certs/tls.key \
    -e MP_DATABASE=/data/mailpit.db -v "$certs:/certs:ro" -v "$mailpit_volume:/data" \
    -p 127.0.0.1:33025:1025 -p 127.0.0.1:8026:8025 "$mailpit_image"
  await_greeting Dovecot 33993 '^\* OK'
  await_greeting Mailpit 33025 '^220'
  echo "dev mail: IMAPS 127.0.0.1:33993, SMTPS 127.0.0.1:33025, sent mail at http://localhost:8026"
}

down() {
  for name in "$dovecot" "$mailpit"; do
    podman container exists "$name" && podman stop -t 2 "$name" >/dev/null
  done
  return 0
}

reset() {
  for name in "$dovecot" "$mailpit"; do
    podman container exists "$name" && podman rm -f "$name" >/dev/null
  done
  podman volume rm -f "$mail_volume" "$mailpit_volume" >/dev/null
  up
}

case "${1:-}" in
  up | down | reset) "$1" ;;
  *)
    echo "usage: $0 up|down|reset" >&2
    exit 2
    ;;
esac
