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
| Component family | mailroom-core and imap-thingy (PyPI); mailroom-mcp not yet ([`../CONTEXT.md`](../CONTEXT.md)) |

Tool pins: `mise.toml` (dev), the digest-pinned `.woodpecker/*.yaml` step images (CI), the
Containerfiles, `backend/.python-version`, `web/package.json` `packageManager`, and
`npm:chrome-devtools-mcp` in `mise.toml` for agent browser verification only. Renovate groups each
tool's pin sites (`renovate.json`).

## Security

**Threat model.** Each user is trusted with their own mail and nobody else's; **mail content is
not trusted at all**. A message is written by a stranger
and may try to run script, track the reader, phish, or — through the MCP — instruct a model.

### HTML mail

- HTML bodies render **only inside a sandboxed iframe**: `sandbox` without `allow-scripts` and
  without `allow-same-origin`, so the message has an opaque origin and can neither run script nor
  reach the app's cookies, storage or API. Never `dangerouslySetInnerHTML` mail content into the
  app's own document.
- The iframe document carries its **own strict CSP** (`default-src 'none'`; images only from
  `data:`/`cid:` parts the API serves, unless the user reveals remote content; no scripts, no
  forms, no frames, no `base`), in addition to server-side sanitisation of the HTML.
- **No remote content by default.** Remote images, fonts and CSS are blocked until the user reveals
  them for that message; tracking pixels therefore never fire on open. Links open in a new
  top-level context with `noopener` and `noreferrer`.
- Plain-text rendering is the fallback and the default for the index.

### The app

- **Separate users.** Every account, filter and suggestion belongs to one user, and every query
  that reads them is scoped to the signed-in user. Admins manage users, not their mail. No sharing.
  See *Sign-in* below.
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

### Sign-in

- **Users** (`app.user`): a username, stored lowercase (1-64 of `a-z 0-9 . _ -`, starting with a
  letter or digit), an Argon2id password hash (passwords of 12-1024 characters), and an admin flag. `mise run create-user`
  makes one; startup warns while there are none. An unknown username still checks a dummy hash, and
  both failures answer the same 401, so neither timing nor wording tells whether a user exists.
- **Sessions** (`app.user_session`): one per signed-in browser, deleted with their user. The
  cookie `mailroom_session` carries a random token; the database keeps only its SHA-256. An absolute
  lifetime of `MAILROOM_SESSION_TTL_DAYS` (default 14), no sliding renewal; signing out deletes the
  row, and a login sweeps expired ones. Every route but health, readiness, version, login, logout
  (idempotent, so an expired tab can still sign out) and `auth/me` (which answers `{"user": …}`)
  depends on `require_user` (`backend/mailroom/api/deps.py`) and answers 401 without a valid
  session.
- **Cookie:** `HttpOnly`, `SameSite=Strict`, `Path=/`, and `Secure` except with
  `MAILROOM_ENV=development` (the dev server is plain HTTP).
- **CSRF:** no token. Same origin plus `SameSite=Strict`, and `CrossSiteRequestMiddleware` refuses
  (403) a state-changing `/api/` request whose `Sec-Fetch-Site` is not `same-origin`, or, without
  that header, whose `Origin` names another host. Add a token only if the API ever serves another
  origin.
- **Login throttling:** after n consecutive failures a client waits 2^(n-1) s, capped at 15 min,
  before an attempt is even checked (429 with `Retry-After`); an attempt counts from the moment it
  is let through, so parallel guesses wait too, and a success resets it. A client is an IPv4
  address or an IPv6 /56: the last address the host's reverse proxy appended to `X-Forwarded-For`,
  which the web container's nginx passes on and uvicorn trusts from loopback. In memory, per API
  process.
- **Managing users** (`/api/v1/users`, admins only: 401 signed out, 403 otherwise): add,
  remove, reset a password. Nobody removes themselves, and a removal locks both users' rows in id
  order first, so two admins removing each other at once leave one: there is always an admin. A
  reset ends all of that user's sessions, and is not for the admin's own password; removing a
  user deletes their sessions with them. A reset lets an admin sign in as that user, so it is
  logged; a reset that the user must complete themselves is a later option, not a promise.
- **Own password** (`POST /api/v1/auth/password`): needs the current one, checked through the
  login throttle (the same per-client counter as sign-in) so a borrowed session cannot guess it;
  keeps this session and ends the others.
- **A session ending under the user:** any 401 from the API but a refused sign-in signs the web
  app out (`onSessionEnded` in `web/src/api/http.ts`), and the sign-in screen says the session
  has ended.
- **Refusals** about users answer 400 with `{"detail": {"code", "message"}}`; the web app words
  each `code` itself, in the reader's language.
- **Logs:** `login_succeeded` (with `user_id`), `login_failed`, `logout`, `user_created`,
  `user_removed`, `password_reset` (with `user_id`, the admin, and `target_user_id`),
  `password_changed`, `password_change_failed`; never a password, a token, or a username.

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
  xdist worker clones it. A test that writes goes through the `db_session` fixture: one outer
  transaction rolled back afterwards, the code under test committing to savepoints; `api_client`
  binds the routes to it.
- **Web:** Vitest; `*.node.test.ts(x)` in bare Node, everything else in jsdom with
  `src/test/setup.ts`. Test by accessible role and label.
- No end-to-end browser test framework; browser checks by agents use chrome-devtools-mcp
  (`AGENTS.md` — *Manual / browser-based testing*), not CI.

## Internationalisation

English is the source language; Italian is kept in parity (`mise run check-web` runs `i18n:check`).
Locales live in `web/src/locales/<lng>/<namespace>.json`.

## CI/CD and releases

[`ci-cd.md`](ci-cd.md).
