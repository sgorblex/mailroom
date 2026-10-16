# CI/CD and release engineering

## Repository layout and versioning model

One repository, one **SemVer** line for backend and web together. **Release Please** (simple
release type, `release-please-config.json`) owns `version.txt`, `CHANGELOG.md`, and the copies in
`backend/pyproject.toml`, `backend/uv.lock` and `web/package.json` (`extra-files`);
`mise run check-version-sync` asserts they agree. Pre-1.0, a `feat` bumps the minor.

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
| [`backend-tests.yaml`](../../.woodpecker/backend-tests.yaml) | pytest against a detached Postgres step, then the migration round trip | `mise run test-backend`, `mise run check-migrations` |
| [`web.yaml`](../../.woodpecker/web.yaml) | ESLint, i18n parity, production build | `mise run check-web` |
| [`web-tests.yaml`](../../.woodpecker/web-tests.yaml) | Vitest | `mise run test-web` |
| [`scripts.yaml`](../../.woodpecker/scripts.yaml) | `node --test` over `scripts/lib`; version sync (also on Release Please PRs) | `mise run check-scripts`, `mise run check-version-sync` |

No release, image or staging pipeline yet: they arrive with the first staging deploy
([`../TODO.md`](../TODO.md)).

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
