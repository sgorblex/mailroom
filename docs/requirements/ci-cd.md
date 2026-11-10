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
| [`backend-tests.yaml`](../../.woodpecker/backend-tests.yaml) | pytest against a detached Postgres step, mailroom-core's also against detached Dovecot and Mailpit steps, then the migration round trip | `mise run test-backend`, `mise run check-migrations` |
| [`web.yaml`](../../.woodpecker/web.yaml) | ESLint, i18n parity, production build | `mise run check-web` |
| [`web-tests.yaml`](../../.woodpecker/web-tests.yaml) | Vitest | `mise run test-web` |
| [`scripts.yaml`](../../.woodpecker/scripts.yaml) | `node --test` over `scripts/lib`; version sync (also on Release Please PRs) | `mise run check-scripts`, `mise run check-version-sync` |

## Release and deploy

[`release.yaml`](../../.woodpecker/release.yaml) runs on a `v*` tag, on the production host's
agent (`linux/arm64`), and only there:

1. **build-push** (`scripts/containers-both.sh production`) builds both images through the docker
   CLI on the agent's Podman socket (so the repository is trusted for volumes), and only once both
   have built pushes `mailroom-{api,web}:<version>`, then `:latest` and `:production`, so a failed
   build never leaves the registry's `:production` split across two releases. A tag that does not
   match `version.txt` fails the build; a pre-release (`v1.2.3-rc.1`) pushes only `:<version>`.
2. **deploy** (`scripts/ssh-deploy.sh`) signs in to the production host as `deploy`, logs root's
   Podman in to the registry and runs `/usr/local/sbin/mailroom-app-deploy`, which pulls both
   `:production` images, checks they are one release, dumps the database to a dated
   `mailroom-pre-<version>-<date>.dump` in the host's backups, restarts `mailroom-pod.service` and
   waits for the API's health check; it fails the step otherwise. If it stops before the restart,
   the running release keeps its images. `deploy` may run only those two commands as root (a sudoers
   rule on the host), with the app's own `restrict`ed key. A pre-release is not deployed.

**One-time setup:** the repository trusted for volumes in Woodpecker, and these secrets, all
limited to tag events: `mailroom_app_registry`, `mailroom_app_registry_user`,
`mailroom_app_registry_password`, `mailroom_app_deploy_host` (`deploy@<host>`),
`mailroom_app_deploy_ssh_key` (this repository's own key, revocable alone) and
`mailroom_app_deploy_known_hosts`. `mailroom_app_registry` must be the bare host that the deploy
host's sudo rule and script name (no scheme, no trailing slash), and
`mailroom_app_registry_user` the user its sudo rule allows; anything else fails the deploy step
at sudo. The host side is the operator's (sudoers, key, script), installed on the host before
the first `v*` tag.

**Rolling back** needs the dump as well as the images, because the API migrates at startup:

1. Stop the pod (`systemctl stop mailroom-pod.service`).
2. Restore the dump taken before the bad release into a fresh database: `dropdb mailroom`,
   `createdb -O mailroom mailroom`, then `pg_restore -d mailroom <dump>` (or `pg_restore --clean
   --create -d postgres <dump>`). Not `pg_restore --clean` into the existing database: it drops
   only what the dump holds, so whatever the bad release's migrations created survives while
   `alembic_version` goes back, and the next upgrade fails on "already exists".
3. Retag **both** `mailroom-api:<previous>` and `mailroom-web:<previous>` as `:production` **in
   the registry** (for example with `skopeo copy`). A retag on the host alone is undone by the next deploy, which pulls
   `:production` again.
4. Pull both images on the host and start the pod.

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
