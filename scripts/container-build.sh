#!/usr/bin/env bash
set -euo pipefail

_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$_repo_root"

_container_engine() {
  if [[ -n "${MAILROOM_CONTAINER_ENGINE:-}" ]]; then
    if ! command -v "${MAILROOM_CONTAINER_ENGINE}" >/dev/null 2>&1; then
      echo "container-build: MAILROOM_CONTAINER_ENGINE=${MAILROOM_CONTAINER_ENGINE} not found on PATH" >&2
      exit 1
    fi
    printf "%s" "${MAILROOM_CONTAINER_ENGINE}"
    return
  fi
  if command -v podman >/dev/null 2>&1; then
    printf "podman"
  elif command -v docker >/dev/null 2>&1; then
    printf "docker"
  else
    echo "container-build: neither podman nor docker was found in PATH; install one or set MAILROOM_CONTAINER_ENGINE (podman|docker)" >&2
    exit 1
  fi
}

# Backend and web share one SemVer line, so both images tag from the same file. Release
# Please owns version.txt and keeps backend/pyproject.toml and web/package.json in step with
# it via `extra-files` - see docs/requirements/ci-cd.md.
_read_semver() {
  local version
  version="$(tr -d '[:space:]' <"$_repo_root/version.txt" 2>/dev/null || true)"
  if [[ -z "$version" ]]; then
    echo "container-build: could not read a version from $_repo_root/version.txt" >&2
    exit 1
  fi
  printf "%s" "$version"
}

# Prefer CI checkout SHA (Woodpecker, then GitHub Actions); otherwise current HEAD when
# building from a git clone.
_git_sha_for_build() {
  if [[ -n "${CI_COMMIT_SHA:-}" ]]; then
    printf "%s" "${CI_COMMIT_SHA}"
    return
  fi
  if [[ -n "${GITHUB_SHA:-}" ]]; then
    printf "%s" "${GITHUB_SHA}"
    return
  fi
  local sha
  sha="$(git -C "$_repo_root" rev-parse HEAD 2>/dev/null || true)"
  printf "%s" "${sha}"
}

# Prefer CI ref name (branch/tag, no refs/heads/ prefix); otherwise current branch from a git
# clone. A tag build is a detached checkout, so both the CI vars and `git rev-parse` fall back
# to the tag itself rather than reporting the literal "HEAD".
_git_branch_for_build() {
  local ref
  for ref in "${CI_COMMIT_BRANCH:-}" "${CI_COMMIT_TAG:-}" "${GITHUB_REF_NAME:-}"; do
    if [[ -n "$ref" ]]; then
      printf "%s" "$ref"
      return
    fi
  done
  local branch
  branch="$(git -C "$_repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ "$branch" == "HEAD" ]]; then
    branch="$(git -C "$_repo_root" tag --points-at HEAD 2>/dev/null | head -1)"
  fi
  printf "%s" "${branch}"
}

_sanitize_tag_component() {
  printf "%s" "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_.-' '-'
}

_build_date() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

# A tag that the next release moves (`:latest`, `:production`, `:staging`) rather than one that
# names a single build.
_is_moving_tag() {
  case "${1##*:}" in
    latest | production | staging) return 0 ;;
    *) return 1 ;;
  esac
}

# Builds the current `tags` array (set by the caller) plus, when MAILROOM_REGISTRY is set, a
# matching set of "${MAILROOM_REGISTRY}/..." tags built in the same invocation and pushed
# afterward. With MAILROOM_PUSH_LIST set as well, the registry tags are not pushed but appended
# to that file as `fixed|moving <engine> <ref>` lines, for containers-both.sh to push once
# every image has built. With MAILROOM_REGISTRY unset, behaves exactly as before (local tags
# only, exec'd so the engine replaces this process for correct signal/exit-code passthrough).
_run_build() {
  local context="$1"; shift
  local -a load_flag=()
  [[ "$E" == "docker" ]] && load_flag=(--load)
  if [[ -n "${MAILROOM_REGISTRY:-}" ]]; then
    local -a registry_tags=()
    local i
    for ((i = 0; i < ${#tags[@]}; i += 2)); do
      registry_tags+=(-t "${MAILROOM_REGISTRY}/${tags[i + 1]#localhost/}")
    done
    tags+=("${registry_tags[@]}")
    "$E" build "$@" "${load_flag[@]}" "${tags[@]}" "$context"
    local ref
    for ((i = 0; i < ${#registry_tags[@]}; i += 2)); do
      ref="${registry_tags[i + 1]}"
      if [[ -z "${MAILROOM_PUSH_LIST:-}" ]]; then
        "$E" push "$ref"
      elif _is_moving_tag "$ref"; then
        printf 'moving %s %s\n' "$E" "$ref" >>"$MAILROOM_PUSH_LIST"
      else
        printf 'fixed %s %s\n' "$E" "$ref" >>"$MAILROOM_PUSH_LIST"
      fi
    done
  else
    exec "$E" build "$@" "${load_flag[@]}" "${tags[@]}" "$context"
  fi
}

E="$(_container_engine)"
GIT_SHA="$(_git_sha_for_build)"
GIT_BRANCH="$(_git_branch_for_build)"
BUILD_DATE="$(_build_date)"

target="${1:-}"
release="${2:-production}"

if [[ "$release" != "production" && "$release" != "staging" ]]; then
  echo "usage: $0 api|web [production|staging]" >&2
  exit 1
fi

# Never `mailroom`: in the production registry, that is the standalone MCP server's image.
case "$target" in
  api | web) image="localhost/mailroom-${target}" ;;
  *)
    printf "%s\n" "usage: $0 api|web [production|staging]" >&2
    exit 1
    ;;
esac

semver="$(_read_semver)"
# A release build tags what the tag says; a tag that disagrees with version.txt is a mistake.
if [[ -n "${CI_COMMIT_TAG:-}" && "${CI_COMMIT_TAG}" != "v${semver}" ]]; then
  echo "container-build: tag ${CI_COMMIT_TAG} does not match version.txt (${semver})" >&2
  exit 1
fi
if [[ "$release" == "production" && "$semver" == *-* ]]; then
  # A pre-release never becomes :production or :latest, so nothing deploys it.
  tags=(-t "${image}:${semver}")
elif [[ "$release" == "production" ]]; then
  tags=(-t "${image}:${semver}" -t "${image}:latest" -t "${image}:production")
else
  tags=(-t "${image}:staging")
  if [[ -n "$GIT_BRANCH" && -n "$GIT_SHA" ]]; then
    tags+=(-t "${image}:staging-$(_sanitize_tag_component "$GIT_BRANCH")-${GIT_SHA:0:7}")
  fi
fi
printf 'container-build: engine=%s target=%s release=%s branch=%s sha=%s tags=%s\n' \
  "$E" "$target" "$release" "${GIT_BRANCH:-<none>}" "${GIT_SHA:-<none>}" "${tags[*]}" >&2

if [[ "$target" == "api" ]]; then
  _run_build . -f backend/Containerfile \
    --build-arg "MAILROOM_GIT_SHA=${GIT_SHA}" --build-arg "MAILROOM_GIT_BRANCH=${GIT_BRANCH}" \
    --build-arg "MAILROOM_VERSION=${semver}" --build-arg "MAILROOM_BUILD_DATE=${BUILD_DATE}"
else
  _run_build ./web -f web/Containerfile \
    --build-arg "VITE_GIT_SHA=${GIT_SHA}" --build-arg "VITE_GIT_BRANCH=${GIT_BRANCH}" \
    --build-arg "VITE_MAILROOM_DEPLOY_ENV=${release}" --build-arg "VITE_VERSION=${semver}" \
    --build-arg "MAILROOM_BUILD_DATE=${BUILD_DATE}"
fi
