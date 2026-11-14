# CI/CD and release engineering

## Repository layout and versioning model

One repository, one **SemVer** line for backend and web together. **Release Please** (simple
release type, `release-please-config.json`) owns `version.txt`, `CHANGELOG.md`, and the copies in
`backend/pyproject.toml`, `backend/uv.lock` and `web/package.json` (`extra-files`);
`mise run check-version-sync` asserts they agree. The first release is 0.1.0 (`initial-version`);
pre-1.0, a `feat` bumps the minor.

**mailroom-core** (`backend/core`) has no release of its own: its changes ship in the app's
releases, like any backend change, and its `version` stays at 0.1.3, its last PyPI release. It
came from mailroom-mcp with its history, through one pull request merged with a **merge commit**
(merge commits are otherwise disabled); that PR's body carries a `BEGIN_COMMIT_OVERRIDE` block, so
Release Please reads all of its commits as one hidden `build:` entry.

[`release-please.yml`](../../.github/workflows/release-please.yml) runs on every push to `main`
and keeps a release PR open. Merging it tags `vX.Y.Z`, and the tag starts the release pipeline
(*Release and deploy* below). The workflow signs in with **`RELEASE_PLEASE_TOKEN`** (a fine-grained
PAT with contents and pull requests read/write on this repository) rather than `GITHUB_TOKEN`,
because GitHub starts no further workflows or webhooks for a tag pushed with `GITHUB_TOKEN`, and
the tag must reach Woodpecker.

## Git workflow and commit conventions

- **Graphite** (**`gt`**) manages branches: **`gt create <type>/<short_name> -m "<type>: …"`**,
  **`gt modify`**, **`gt restack`**, and — only when the user asks — **`gt submit`**. Check the
  current stack (**`gt log`**) before adding branches.
- **Branch naming — `<type>/<short_name>`**: one slash; `<type>` is the Conventional Commits type
  the branch squashes to (`feat`, `fix`, `chore`, `docs`, `refactor`, `perf`, `test`, `ci`,
  `build`, `revert`, `style`); `<short_name>` is lowercase `snake_case`, two to four words. No scope
  segment — scope belongs in the commit title (`feat(web):`). Tool-generated branches
  (`release-please--…`, `renovate/…`, `graphite-base/…`, agent `worktree-…`) are reserved.
- **Conventional Commits** on every commit; the squash-merge title is what Release Please reads.
- **Linear `main`**: squash merges, one commit per PR. A branch ships as one unit, so fix an
  earlier commit of an unmerged branch at the source (`gt modify`) rather than reverting it later.
- **Agents never run remote-mutating commands** (push, submit, PR create/merge/comment, remote repo
  creation, image push, deploy) unless the user asks for that action in the current request — see
  [`../../AGENTS.md`](../../AGENTS.md).
- A stacked PR that cannot pass yet: label it **`ci:pause`** (Woodpecker withholds its runs).

## Continuous integration (required quality gates)

Woodpecker, one file per pipeline, path filters on the **steps** (so every pipeline always
reports), Release Please branches skipped by `evaluate`. How to work on them:
[`.agents/skills/woodpecker/SKILL.md`](../../.agents/skills/woodpecker/SKILL.md).

| Pipeline | Runs | Local equivalent |
| --- | --- | --- |
| [`backend.yaml`](../../.woodpecker/backend.yaml) | `ruff check`, `ruff format --check`, `mypy` | `mise run check-backend` |
| [`backend-tests.yaml`](../../.woodpecker/backend-tests.yaml) | pytest against a detached Postgres step, the app's and mailroom-core's mail tests also against detached Dovecot and Mailpit steps, then the migration round trip | `mise run test-backend-mail`, `mise run test-core-mail`, `mise run check-migrations` |
| [`web.yaml`](../../.woodpecker/web.yaml) | ESLint, i18n parity, production build | `mise run check-web` |
| [`web-tests.yaml`](../../.woodpecker/web-tests.yaml) | Vitest | `mise run test-web` |
| [`scripts.yaml`](../../.woodpecker/scripts.yaml) | `node --test` over `scripts/lib`; version sync (also on Release Please PRs) | `mise run check-scripts`, `mise run check-version-sync` |

## Release and deploy

[`release.yaml`](../../.woodpecker/release.yaml) runs on a `v*` tag, on the production host's
agent (`linux/arm64`), and only there. **prepare** (`scripts/release-prepare.sh`) checks the tag
against `version.txt` (a mismatch fails the release) and writes the image tag and build arguments
for the next steps. **build-api** and **build-web** build both images without pushing, and only then
do **push-api** and **push-web** push `mailroom-{api,web}:<version>`, so a failed build pushes
nothing. No `:latest`: the host pins versions. The builds run in `plugin-docker-buildx`, which the
Woodpecker server lets run privileged (`WOODPECKER_PLUGINS_PRIVILEGED`, exact tag and digest: bump
it there before the pipeline), so the repository needs no trust; BuildKit's cache is kept in the
registry as `mailroom-{api,web}:buildcache`, which the push steps hit. Each image carries the
`org.opencontainers.image.version`, `.revision` (the full commit SHA), `.created` and `.source`
labels. A manual run of the pipeline builds both images without pushing, to test it.

**Nothing deploys from CI.** The host's config repository (oci-server-conf) pins both images as
`<version>@sha256:…`. After a release, Renovate opens a "Mailroom" PR there, and merging it
deploys: the host dumps the `mailroom` database to its backups, restarts the pod, and checks the
API's health, that `/api/v1/version` reports the new version, and that both images carry the same
`org.opencontainers.image.version`. So a release is: merge the Release Please PR (which tags it),
then merge the Renovate PR. The host side is documented in oci-server-conf
(`docs/services/Mailroom.md`).

**One-time setup:** these secrets, limited to tag and manual events: `mailroom_app_registry_user`
and `mailroom_app_registry_password`. The registry host, `reg.aclerici.me`, is written in the
pipeline: plugin settings can't combine a secret with other text.

**Rolling back** is reverting that Renovate PR, which deploys the previous images. If the bad
release migrated the database (the API migrates at startup), restore the dump taken just before it
too, from `/srv/backups`:

1. Stop the pod (`systemctl stop mailroom-pod.service`).
2. Restore the dump into a fresh database: `dropdb mailroom`, `createdb -O mailroom mailroom`, then
   `pg_restore -d mailroom <dump>` (or `pg_restore --clean --create -d postgres <dump>`). Not
   `pg_restore --clean` into the existing database: it drops only what the dump holds, so whatever
   the bad release's migrations created survives while `alembic_version` goes back, and the next
   upgrade fails on "already exists".
3. Revert the Renovate PR, which starts the pod on the previous images.

Staging is still to come ([`../TODO.md`](../TODO.md)).

## Repository automation

**Every PR has an assignee:** [`assignee.yml`](../../.github/workflows/assignee.yml) assigns the
author when a PR opens. It is a **GitHub Actions** workflow, not a Woodpecker pipeline, because it
only edits PR metadata: `pull_request_target` gives it a write token without a checkout. It is
**not a required check**, and it skips Release Please branches.

## Dependency updates

Renovate (`renovate.json`): dashboard approval, a 7-day soak matching
`web/pnpm-workspace.yaml`'s `minimumReleaseAge`, digest-pinned images and GitHub Actions, and one
group per tool across its pin sites.

## Container images

[`../deploy.md`](../deploy.md).
