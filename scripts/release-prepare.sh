#!/bin/sh
# First step of .woodpecker/release.yaml (POSIX sh: it runs in alpine). Writes what the buildx plugin
# steps read: .tags (the image tag) and .build.env (build arguments, passed with env_file and
# build_args_from_env). Same values as scripts/container-build.sh gives a production build, minus
# :latest: the server pins versions. A manual run (a test build, nothing pushed) skips the tag check.
set -eu

semver="$(tr -d '[:space:]' < version.txt)"
[ -n "$semver" ] || { echo "release-prepare: no version in version.txt" >&2; exit 1; }
if [ -n "${CI_COMMIT_TAG:-}" ] && [ "$CI_COMMIT_TAG" != "v$semver" ]; then
  echo "release-prepare: tag $CI_COMMIT_TAG does not match version.txt ($semver)" >&2
  exit 1
fi
branch="${CI_COMMIT_TAG:-${CI_COMMIT_BRANCH:-}}"

printf '%s\n' "$semver" > .tags
cat > .build.env <<ENV
MAILROOM_GIT_SHA=${CI_COMMIT_SHA}
MAILROOM_GIT_BRANCH=${branch}
MAILROOM_VERSION=${semver}
MAILROOM_BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
VITE_GIT_SHA=${CI_COMMIT_SHA}
VITE_GIT_BRANCH=${branch}
VITE_MAILROOM_DEPLOY_ENV=production
VITE_VERSION=${semver}
ENV
echo "release-prepare: tag $semver"; cat .build.env
