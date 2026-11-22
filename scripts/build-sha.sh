#!/bin/sh
# Steps of .woodpecker/build-sha.yaml around plugin-git's clone of MAILROOM_SHA into target/:
# `validate` before it, `prepare` after it. POSIX sh (alpine), no network. `prepare` writes what the
# buildx steps read, target/.ci/tags (git-<sha7>) and target/.ci/build.env, from this checkout's
# copy of the script, so any older commit builds the same way.
set -eu

fail() {
  echo "build-sha: $*" >&2
  exit 1
}

sha="${MAILROOM_SHA:-}"

case "${1:-}" in
  validate)
    # plugin-git fetches a full sha from any ref; a short one it can only find on main.
    case "$sha" in
      "" | *[!0-9a-f]*) fail "MAILROOM_SHA must be a full 40-character lowercase hex sha, got '${sha}'" ;;
    esac
    [ "${#sha}" -eq 40 ] || fail "MAILROOM_SHA must be a full 40-character sha, got '${sha}'"
    ;;
  prepare)
    head="$(git -C target rev-parse HEAD)"
    [ "$head" = "$sha" ] || fail "target/ is at ${head}, not ${sha}"
    semver="$(tr -d '[:space:]' < target/version.txt)"
    [ -n "$semver" ] || fail "no version in target/version.txt"
    # The revision label must be the full sha: the host refuses an image without it.
    mkdir -p target/.ci
    printf 'git-%s\n' "$(printf '%s' "$sha" | cut -c1-7)" > target/.ci/tags
    cat > target/.ci/build.env <<ENV
MAILROOM_GIT_SHA=${sha}
MAILROOM_GIT_BRANCH=staging
MAILROOM_VERSION=${semver}
MAILROOM_BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
VITE_GIT_SHA=${sha}
VITE_GIT_BRANCH=staging
VITE_MAILROOM_DEPLOY_ENV=
VITE_VERSION=${semver}
ENV
    echo "build-sha: building ${head} as $(cat target/.ci/tags)"
    cat target/.ci/build.env
    ;;
  *)
    fail "usage: build-sha.sh validate|prepare"
    ;;
esac
