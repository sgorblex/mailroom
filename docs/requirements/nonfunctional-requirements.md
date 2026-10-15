# Non-functional requirements

Engineering baseline for Mailroom. Product scope: [`../REQUIREMENTS.md`](../REQUIREMENTS.md).

## Implementation stack (reference)

| Area | Choice |
| --- | --- |
| Backend | Python 3.14+ (dev pin 3.15 via mise), FastAPI, SQLAlchemy 2 (sync) + psycopg 3, Alembic, uv |
| Web | React 19, Vite, TanStack Router (code-based routes), Tailwind 4 + shadcn (`web/components.json`), i18next, Zod at the API boundary, ESLint `strictTypeChecked` (no Prettier) |
| Database | PostgreSQL 18 |
| Tests | pytest (+ xdist, one database per worker cloned from the migrated `mailroom_test`), Vitest (`node` and `jsdom` projects) |
| Containers | Podman, `mailroom-api` and `mailroom-web` images; Quadlet for deploy |
| Component family | mailroom-core, mailroom-mcp, imap-thingy — not yet dependencies ([`../CONTEXT.md`](../CONTEXT.md)) |

Tool pins: `mise.toml` (dev), the digest-pinned `.woodpecker/*.yaml` step images (CI), the
Containerfiles, `backend/.python-version`, `web/package.json` `packageManager`, and
`npm:chrome-devtools-mcp` in `mise.toml` for agent browser verification only. Renovate groups each
tool's pin sites (`renovate.json`).

## Security

**Threat model.** The owner is trusted; **mail content is not**. A message is written by a stranger
and may try to run script, track the reader, phish, or — through the MCP — instruct a model.

### HTML mail

- HTML bodies render **only inside a sandboxed iframe**: `sandbox` without `allow-scripts` and
  without `allow-same-origin`, so the message has an opaque origin and can neither run script nor
  reach the app's cookies, storage or API. Never `dangerouslySetInnerHTML` mail content into the
  app's own document.
- The iframe document carries its **own strict CSP** (`default-src 'none'`; images only from
  `data:`/`cid:` parts the API serves, unless the owner reveals remote content; no scripts, no
  forms, no frames, no `base`), in addition to server-side sanitisation of the HTML.
- **No remote content by default.** Remote images, fonts and CSS are blocked until the owner reveals
  them for that message; tracking pixels therefore never fire on open. Links open in a new
  top-level context with `noopener` and `noreferrer`.
- Plain-text rendering is the fallback and the default for the index.

### The app

- **Single owner.** One account, one session; no roles, no sharing. Every route except health,
  readiness and version will require the owner's session (cookie: `HttpOnly`, `Secure`,
  `SameSite`).
- **Strict CSP on the app** (`web/nginx-default.conf`): `script-src 'self'` with no inline script
  (the dark theme follows `prefers-color-scheme` in CSS for that reason), `frame-ancestors 'none'`,
  `object-src 'none'`, `base-uri 'none'`, plus `nosniff` and `Referrer-Policy: no-referrer`.
  `style-src` allows `'unsafe-inline'` for UI-library style attributes (decided; revisit if that
  stops being needed).
- The SPA and API are **same-origin** (Vite proxy in dev, nginx in the image), so there is no CORS
  configuration at all.
- **Secrets** (account passwords, tokens) are sealed at rest as mailroom-core seals them and never
  appear in logs, errors or API responses.
- **Destructive actions** never expunge: trash moves to `\Trash`, as in mailroom-mcp.
- **Learning never acts** ([`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *Filter suggestions*).

## Data persistence & schema migrations

- All app tables live in PostgreSQL schema **`app`** (`Base.metadata` schema). Alembic's version
  table is **`app.alembic_version`**, so mailroom-mcp's own history (schema `mcp`) can share the
  database without either seeing the other's. `alembic/env.py` creates the schema before Alembic
  writes its version table; autogenerate only looks at `app`.
- On the production host the database and role are **`mailroom`**; standalone mailroom-mcp uses **`mailroom_mcp`**.
- Revisions are **timestamped** (`YYYYMMDDHHMM`, file `<rev>_<slug>.py`) and form one linear chain.
- Every revision **downgrades**: `mise run check-migrations` runs upgrade → downgrade base →
  upgrade on a scratch database, locally and in CI.
- The API migrates at startup (`MAILROOM_RUN_STARTUP_MIGRATIONS=1` in the image and in dev); run a
  single API worker, or migrate externally before scaling.
- An unmerged branch's migrations are edited in place, never followed by a fixing migration;
  merged revisions are immutable.

## Structured logging

Logs go to stderr: JSON lines in production, one human-readable line per record in development
(`MAILROOM_LOG_FORMAT`, `MAILROOM_LOG_LEVEL`). Every HTTP request gets an `X-Request-ID` (accepted
from the client when sane) carried on each record. Never log message subjects, bodies or secrets in
clear (hash them, as mailroom-mcp's audit does).

## Automated testing

- **Backend:** pytest against real PostgreSQL (`TEST_DATABASE_URL`, loopback and `_test` only unless
  `MAILROOM_INTEGRATION_TESTS_ALLOW_ANY_DATABASE=1`). The coordinator migrates `mailroom_test`; each
  xdist worker clones it.
- **Web:** Vitest; `*.node.test.ts(x)` in bare Node, everything else in jsdom with
  `src/test/setup.ts`. Test by accessible role and label.
- No end-to-end browser test framework; browser checks by agents use chrome-devtools-mcp
  (`AGENTS.md` — *Manual / browser-based testing*), not CI.

## Internationalisation

English is the source language; Italian is kept in parity (`mise run check-web` runs `i18n:check`).
Locales live in `web/src/locales/<lng>/<namespace>.json`.

## CI/CD and releases

[`ci-cd.md`](ci-cd.md).
