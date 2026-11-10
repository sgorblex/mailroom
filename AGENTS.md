# Agent notes — Mailroom

This file is the **shared** guide for AI-assisted work in this repository. **Claude**-oriented clients read **`CLAUDE.md`**, which redirects here.

**Product intent and capabilities** live under **`docs/`**, especially **`docs/CONTEXT.md`** (what Mailroom is, the component family, non-goals) and **`docs/REQUIREMENTS.md`**. The root **`README.md`** introduces the project and lists the tasks.

## Before you implement

**Cheap path:** **[`docs/README.md`](docs/README.md)** (which doc to open next), then only the **REQUIREMENTS** section you are changing plus any linked split file. Locate with **`grep -n`**, then read that range.

1. Match behaviour to **`docs/REQUIREMENTS.md`**. **Maturity** (**Planned** / **Current** / **Partial** / **Done**) tracks what we are building; clarify with the user if a section is **Current** but intentionally not implemented yet.
2. Respect the **component boundaries** in **`docs/CONTEXT.md`**: dependencies only point **downwards**. **mailroom-core** (`backend/core`, a workspace package) never imports `mailroom`; imap-thingy and mailroom-mcp are **standalone projects** the app never makes depend on it. A change that needs a lower component to know about the app is a design question for the user, not an import.
3. For **stack, security, migrations, logging, or testing**, read **`docs/requirements/nonfunctional-requirements.md`** and keep it aligned with what you implement. **Mail content is untrusted input** — the security section there is not optional reading for anything that displays or acts on mail.
4. For **CI/CD, SemVer, changelog, or release automation**, read **`docs/requirements/ci-cd.md`**.
5. For **container images or Quadlet**, read **`docs/deploy.md`** and **`deploy/quadlet/README.md`**. Image names are **`mailroom-api`** and **`mailroom-web`** — never **`mailroom`**, which is the MCP server's production image.
6. Resolve domain wording with **`docs/GLOSSARY.md`** instead of inventing new terms.

**Learning never acts.** Anything that learns from a user's behaviour produces **suggestions** that user accepts or dismisses; it never moves, flags, deletes or sends mail on its own, and never enables a filter by itself. Do not add an "auto-apply" path unless the user explicitly asks for that work.

**Git worktrees:** feature-sized agent work belongs in its **own git worktree** under **`.claude/worktrees/`** (gitignored, one per task) — if the harness placed you in one, that one is yours. From the **shared main checkout**, **ask the user before creating one** (**`git worktree add .claude/worktrees/<short_name>`**). Inside the worktree, work on a **real Graphite branch** named per the convention below — a harness-generated **`worktree-…`** branch is scratch, and **`gt` refuses to operate on it**. **Open the real branch first:** **`git switch -c <type>/<short_name>`** then **`gt track --parent main`** (or the branch you are stacking on), after which **`gt create`** stacks normally; then **`git branch -d worktree-…`**. A **`SessionEnd`** hook in **`.claude/settings.json`** sweeps leftover empty ones. Hand the worktree back **in place**, everything **committed**, working tree **clean**, nothing submitted. Full recipe: **[`.agents/skills/start-work/SKILL.md`](.agents/skills/start-work/SKILL.md)**.

**Git / Graphite:** branches are managed with **Graphite** (**`gt`**) and named **`<type>/<short_name>`** — lowercase **`snake_case`**, **`<type>`** being the **Conventional Commit** type the branch will squash to (**`feat/thread_view`**, **`fix/idle_reconnect`**). Pass that name to **`gt create`** explicitly; commit messages follow **Conventional Commits** too. Full rules in **[`docs/requirements/ci-cd.md`](docs/requirements/ci-cd.md)** (**Git workflow and commit conventions**). Check the **current stack** (**`gt log`**) before adding branches. **Never run remote-mutating commands — `git push`, `gt submit`, `gt push`, `gh pr create`, creating a remote repository, merging, or closing/commenting on a PR, or anything that writes to a server or registry (an image push, a deploy) — unless the user explicitly asks for that action in the current request.** Approval of a plan is not that request. Local commits, stack edits (**`gt create`**, **`gt modify`**, **`gt restack`**), and read-only inspection do not require asking. Put the **why** in the **commit message body**: the review pass runs in a fresh session and reads the branch, not the conversation.

## Mail invariants

They hold across mailroom-core and its callers (the web client, the MCP mail tools); filters run through imap-thingy instead. Each is specified in **[`docs/core.md`](docs/core.md)**; this is the checklist, not the spec.

- **One send gate.** Every send, reply, forward and draft send goes through `admit`, with `by` saying whether the user or an agent acts; nothing else reaches SMTP.
- **`from` resolution:** an explicit `from` must be one of the account's identities; else the original's To, Cc, then delivery headers; else the default. The MCP tools always echo the choice.
- **Message refs** encode account, folder, UIDVALIDITY and UID. A stale ref is an error, never a different message.
- **Each user's own accounts only.** Refs and account ids are not signed: the app checks every one is the caller's before core touches it.
- **Email content is the adversary.** Mail-derived text an agent reads goes inside the untrusted-content envelope; CRLF is stripped from any header built from input; no remote fetches; server and library errors become `MailError`'s fixed messages; secrets never reach a log line, an error or a tool result.
- **No destructive deletes.** Trash moves to `\Trash`. Nothing is expunged but what a move has copied elsewhere (UIDPLUS, exactly those UIDs) and a draft Mailroom wrote itself (`X-Mailroom-Draft`) when it is replaced or deleted. Filters are the one exception: their `delete` action is the user's own rule ([`docs/requirements/filters.md`](docs/requirements/filters.md)).
- **Provider quirks by capability, not hostname** (Gmail is `X-GM-EXT-1`).
- **Audit:** core yields an item for every write and send attempt, refused ones included, senders and recipients in clear, subjects only as a hash; the app keeps each as an audit record (`mailroom.audit`), and a new write path passes its trail; only the web's draft saves drop them (`kept_nowhere`: the user typing).

## Shell commands (mise)

**mise** pins **Python**, **uv**, **Node**, and **pnpm** (**`mise.toml`**). Agent shell sessions do **not** have those tools on **`PATH`** unless **mise** is active — **`uv: command not found`** usually means you skipped **mise**.

From the **repository root**:

- **`mise run <task>`** — preferred; task index in **`README.md`** and **`mise.toml`**.
- **`mise exec -- …`** — ad-hoc invocations, e.g. **`mise exec -- uv run --directory backend pytest tests/test_api_health.py`**.

Common mappings: **`uv sync`** → **`mise run setup`**; **`pytest`** → **`mise run test-backend`** (needs **`mise run dev-db`** running first); **`ruff`** / **`mypy`** → **`mise run check-backend`** or **`fix`**; **`alembic upgrade head`** → **`mise run migrate`**; web lint / test / build → **`mise run check-web`** / **`test-web`**.

Do **not** invoke **`uv`**, **`pnpm`**, **`pytest`**, **`ruff`**, **`mypy`**, or **`alembic`** directly without **`mise run`** or **`mise exec`**.

**Dev environment defaults** (ports, **`DATABASE_URL`**, **`TEST_DATABASE_URL`**) live in **`mise.toml`**'s **`[env]`**, and they win over anything you export on the command line and over a root **`.env`** (which only adds keys `mise.toml` lacks). **Override one in a gitignored `mise.local.toml`** (`[env]`), the only file that wins over `mise.toml`; check with **`mise exec -- printenv DATABASE_URL`** before any task that drops or resets a database. Ports are **fixed** (API **8100**, Vite **5180**, Postgres **5434**, dev Dovecot **33993**, Mailpit **33025** / **8026**, dev OIDC provider **8190**), chosen not to clash with other local dev stacks or mailroom-mcp. There is **no per-worktree registry**: every checkout shares one dev database and one set of ports, so check whether something is already running before you start it, reuse it, and stop only what **you** started.

## Local quality checks

When verifying changes, run **`mise run fix`**, **`mise run check-backend`** / **`check-web`**, and **`mise run test-backend`** / **`test-web`** as scope dictates; **`check-migrations`** for a migration. Report work as done only once the relevant check has **actually run**, and say plainly which ones did not.

**Cadence.** After each implementation pass, the fast ones only — **`mise run fix`**, plus **`check-web-types`** or **`check-backend`**. Add the scoped **`test-*`** before handing work over. The full set runs once, at the pre-PR review (**[`review-stack`](.agents/skills/review-stack/SKILL.md)**).

## Manual / browser-based testing

Clicking through a feature in a real browser uses **`chrome-devtools-mcp`**, configured in **`.mcp.json`** and launched lazily by **`scripts/browser-mcp.mjs`** — a verification aid against a running dev instance, **not** a test suite or a CI gate. Recipe and troubleshooting: **[`.agents/skills/browser-verification/SKILL.md`](.agents/skills/browser-verification/SKILL.md)**. Never point **`MAILROOM_BROWSER_URL`** / **`MAILROOM_BROWSER_HEADFUL`** at the user's everyday browser.

## AI-agent skills

Canonical skill content lives under **`.agents/skills/<name>/`**. **`.claude/skills/<name>`** is a **relative symlink** back to it, never a second copy; edit the `.agents/skills` original. Fetched skills are tracked with provenance in **`skills-lock.json`** (today: **`design-taste-frontend`**); locally-authored ones are not lock-tracked. **Fetched skill files are committed on purpose** — the lock records a hash but no ref, and a restore silently returns whatever upstream holds now.

## After you change the repository

Update documentation in the **same change**. Prefer **links** over duplicating a paragraph. **Write as little as the rule needs** — no comments restating the code (CI YAML especially), no documentation beyond what the change requires.

| You change… | Update… |
| --- | --- |
| User-visible behaviour or agreed product scope | **`docs/REQUIREMENTS.md`** (maturity, notes, open questions), or a linked **`docs/requirements/<kebab-topic>.md`** for a new feature area |
| What Mailroom is for, the component family, or a non-goal | **`docs/CONTEXT.md`** |
| A dependency on imap-thingy or mailroom-mcp, or what mailroom-core offers the app | **`docs/CONTEXT.md`** (component family) and **`docs/requirements/nonfunctional-requirements.md`** (stack) |
| mailroom-core's behaviour or design (`backend/core`) | **`docs/core.md`**; **Mail invariants** above if one changes |
| Security engineering, stack, migrations, logging, testing | **`docs/requirements/nonfunctional-requirements.md`** |
| CI pipelines, versioning, changelog, release automation | **`docs/requirements/ci-cd.md`** |
| **Containerfile**s, **`.dockerignore`**, **`deploy/quadlet/`** | **`docs/deploy.md`**, **`deploy/quadlet/README.md`** |
| A task in **`mise.toml`**, a dev port, or a dev service | Root **`README.md`** (task and port tables); this file if an agent rule changes |
| A new **`MAILROOM_*`** / **`VITE_*`** environment variable | **`.env.example`**; **`deploy/quadlet/mailroom-api.env.example`** when operator-facing |
| Browser-verification tooling (**`.mcp.json`**, **`scripts/browser-mcp.mjs`**) | **Manual / browser-based testing** above; the **browser-verification** skill |
| A convention agreed with the user **in conversation** | This file, the matching **`docs/`** file, or a skill — in the same change |
| A **`docs/TODO.md`** item shipped, picked up, or reprioritised; an idea raised or dropped | **`docs/TODO.md`**, **`docs/IDEAS.md`** |
| Versions or **`CHANGELOG.md`** | **Nothing by hand** — **Release Please** owns them |
| Domain vocabulary | **`docs/GLOSSARY.md`** |

Which doc holds what: **[`docs/README.md`](docs/README.md)**.
