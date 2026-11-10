#!/usr/bin/env bash
# Deploys the images the release just pushed: on the production host, as the `deploy` user, log
# the root podman in to the registry and run mailroom-app-deploy, which pulls both :production
# images, checks they are one release, dumps the database, restarts the pod and waits for the
# API's health check. Run by .woodpecker/release.yaml; the sudo rules and the script live on the
# host (docs/requirements/ci-cd.md).
set -euo pipefail

: "${MAILROOM_DEPLOY_HOST:?MAILROOM_DEPLOY_HOST must be set (user@host)}"
: "${MAILROOM_DEPLOY_SSH_KEY:?MAILROOM_DEPLOY_SSH_KEY must be set (private key contents)}"
: "${MAILROOM_DEPLOY_KNOWN_HOSTS:?MAILROOM_DEPLOY_KNOWN_HOSTS must be set}"
: "${MAILROOM_REGISTRY:?MAILROOM_REGISTRY must be set}"
: "${MAILROOM_REGISTRY_USER:?MAILROOM_REGISTRY_USER must be set}"
: "${MAILROOM_REGISTRY_PASSWORD:?MAILROOM_REGISTRY_PASSWORD must be set}"

if [[ "${CI_COMMIT_TAG:-}" == *-* ]]; then
  echo "ssh-deploy: ${CI_COMMIT_TAG} is a pre-release, not deploying"
  exit 0
fi

keyfile="$(mktemp)"
knownhosts="$(mktemp)"
trap 'rm -f "$keyfile" "$knownhosts"' EXIT
printf '%s\n' "$MAILROOM_DEPLOY_SSH_KEY" > "$keyfile"
chmod 600 "$keyfile"
printf '%s\n' "$MAILROOM_DEPLOY_KNOWN_HOSTS" > "$knownhosts"

# The password goes over stdin, never into the remote command line.
remote_cmd="sudo podman login '$MAILROOM_REGISTRY' -u '$MAILROOM_REGISTRY_USER' --password-stdin \
  && sudo /usr/local/sbin/mailroom-app-deploy"

ssh -i "$keyfile" -o UserKnownHostsFile="$knownhosts" -o StrictHostKeyChecking=yes \
  "$MAILROOM_DEPLOY_HOST" "$remote_cmd" <<< "$MAILROOM_REGISTRY_PASSWORD"
