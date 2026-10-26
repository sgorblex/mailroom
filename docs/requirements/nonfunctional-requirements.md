# Non-functional requirements

Engineering baseline for Mailroom. Product scope: [`../REQUIREMENTS.md`](../REQUIREMENTS.md).

## Implementation stack (reference)

| Area | Choice |
| --- | --- |
| Backend | Python 3.14+ (dev pin 3.15 via mise), FastAPI, SQLAlchemy 2 (sync) + psycopg 3, Alembic, uv |
| Worker | procrastinate (psycopg 3) on the app's database, `python -m mailroom.worker`; it also watches folders over IMAP IDLE |
| Web | React 19, Vite, TanStack Router (code-based routes), Tailwind 4 + shadcn (`web/components.json`), i18next, Zod at the API boundary, ESLint `strictTypeChecked` (no Prettier) |
| Database | PostgreSQL 18 |
| Tests | pytest (+ xdist, one database per worker cloned from the migrated `mailroom_test`), Vitest (`node` and `jsdom` projects) |
| Containers | Podman, `mailroom-api` and `mailroom-web` images; Quadlet for deploy |
| Component family | mailroom-core and imap-thingy (PyPI); mailroom-mcp stays standalone, not a dependency ([`../CONTEXT.md`](../CONTEXT.md)) |

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
- **One service layer.** What a user may do with their accounts and filters (ownership by user id,
  the checks, the guards) lives in `mailroom.accounts.service` and `mailroom.filters.service`,
  which refuse with a `mailroom.errors.Refusal` (a kind, a `code`, a safe `message`). The REST
  routes only map them (`mailroom.api.refusals.http_refusal`: `not_found` 404 with the message
  alone, `invalid` 400, `conflict` 409, `busy` 429, `mail` 502 or 409, `unavailable` 503,
  `timeout` 504, `failed` 500), so every front end keeps the same rules.
- **Strict CSP on the app** (`web/nginx-default.conf`): `script-src 'self'` with no inline script
  (the dark theme follows `prefers-color-scheme` in CSS for that reason), `frame-ancestors 'none'`,
  `object-src 'none'`, `base-uri 'none'`, plus `nosniff` and `Referrer-Policy: no-referrer`.
  `style-src` allows `'unsafe-inline'` for UI-library style attributes (decided; revisit if that
  stops being needed).
- The SPA and API are **same-origin** (Vite proxy in dev, nginx in the image), so there is no CORS
  configuration at all.
- **Secrets** (account passwords, tokens) are sealed at rest as mailroom-core seals them and never
  appear in logs, errors or API responses. See *Mail accounts* below.
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

### Mail accounts

- **Storage:** mailroom-core's `AccountStore(engine, str(user.id))` over its schema `core` in the
  app's database: one settings document per user (revisions, replaced whole) and the passwords
  sealed beside it (AES-256-GCM under a subkey of `MAILROOM_SECRETS_KEY`, 64 hex digits, bound to
  the account id), byte-compatible with mailroom-mcp. Without the key (or with a malformed one)
  startup warns, the accounts stay listed, and adding, a new password, a test or a folder listing
  answers 503 `secrets_key_missing`. The app's own `app.account_filter_settings(user_id,
  account_id, run_filters)` holds the Run filters switch; no row is off.
- **API** (`/api/v1/accounts`, the signed-in user's own; another user's id is a 404): list, add
  (`POST`, with the password), edit (`PUT /{id}`, without the id; no password keeps it), remove
  (`DELETE /{id}`, its switch too), `POST /{id}/test` (an IMAP login and logout:
  `{"ok", "code", "message"}`), `GET /{id}/folders` (`[{"path": [segments]}]`), `GET`/`PUT
  /{id}/filter-settings` (`{"run_filters"}`). Responses carry `has_password`, never the password.
- **Checks:** mailroom-core's config models (`core_settings_or_issues`) validate the whole document
  before each save; a refusal is 400 `{"detail": {"code": "invalid_account" | "account_exists",
  "message", "issues": [{"path", "message"}]}}`, `path` inside the account. A save racing another
  is 409 `accounts_changed` or 503 `accounts_busy`.
- **IMAP** goes through imap-thingy, implicit TLS only (`starttls_unsupported` otherwise), verified
  against the system CAs plus `MAILROOM_MAIL_CA_FILE` (the dev mail CA in dev), with a 20 s
  timeout on connecting and on every read. A failure is reported by kind (`login_failed`,
  `unreachable`, `timeout`, `tls_failed`, `imap_error`, `password_missing`) with the server's wording, the password masked; a folder
  listing answers it as 502 (409 for `password_missing` / `starttls_unsupported`).
- **Logs:** `account_added`, `account_changed`, `account_removed`, `account_tested` (with
  `outcome`), `run_filters_changed`, each with `user_id` and `account_id`, and the changes with
  `via` (`web`, or `mcp` for the MCP server); never a login, a host or a password.
- **Removing a user** removes, in the same transaction, the app's rows about their accounts and
  mailroom-core's (`forget_user`: settings with their history, sealed passwords, send log). While
  a save of their accounts holds them past mailroom-core's lock timeout, nothing is removed: 503
  `user_accounts_busy`.
- **Known risk — outbound connections:** a login test or a folder listing connects to whatever
  host and port the user entered, so any signed-in user can make the server open a TLS connection
  from its network to any `host:port` (internal services included) and learn from the failure kind
  whether something answered. Accepted because only admin-created users sign in;
  revisit before anyone else can.

### Filters

What they do and the route list: [`filters.md`](filters.md). How:

- **Storage** (schema `app`): `rule_set` (by `user_id` and the account's id in schema `core`),
  `filter` (`position` unique per rule set, deferred so a reorder swaps freely; `spec` as
  imap-thingy's `load_filter(spec).to_spec()` gives it back), `filter_run`. Removing an account
  removes its rule sets, and a rule set its filters and runs; removing a user removes all of
  theirs, with their accounts. Each rule
  set keeps its newest 100 runs, and every run still queued or in progress. A report is stored
  without NUL characters (`jsonb` refuses them; a header may decode to one).
- **Refusals** are 400 `{"detail": {"code", "message"}}`: `invalid_spec` (imap-thingy's
  `SpecError` text, or the JSON Schema's or the regular expression's complaint, compiled with
  `regex` as imap-thingy matches it), `invalid_rule_set` (an `interval_minutes` exactly when
  `scheduled`), `invalid_order` (not every filter once), `too_many_filters` (a rule set holds 100
  at most), `run_filters_off`, `rule_set_disabled`; 409 `run_pending`.
- **Regular expressions** are matched by imap-thingy with the `regex` package, each match bounded
  by its match timeout (`DEFAULT_MATCH_TIMEOUT`, 1 s per message, process-wide): a pattern that
  backtracks past it raises `MatchTimeoutError`, which fails the run with `match_timeout`.
- **Dry run** (`POST /rule-sets/{id}/dry-run`), synchronous: the enabled filters, wrapped for
  starred only, run through imap-thingy's `Folder.run(dry_run=True)` (read-only select) on the
  folder's newest 500 messages, for about 30 s (then the connection is closed: 504
  `dry_run_timeout`). A pattern out of time is 400 `match_timeout`. The folder path is joined with
  the delimiter the server reports. Mailbox failures answer as a folder listing does (502, or 409
  `password_missing` / `starttls_unsupported`, 503 `secrets_key_missing`); any other failure is 500
  `run_failed`, its exception's type name only. Either way the run joins the history. One dry run
  per user at a time, across API processes: a session advisory lock on a connection of its own,
  taken with `pg_try_advisory_lock`; a second one meanwhile is 429 `dry_run_busy` and not
  recorded.
- **Queueing a run** — Run now (`POST /rule-sets/{id}/run`, 202), the scheduled dispatcher, new
  mail — inserts a queued `filter_run` (no `started_at`) and, in the same transaction, the
  procrastinate job `run_rule_set(run_id)` with `lock` `mailbox:<user id>:<account id>` (one job
  per mailbox at a time) and `queueing_lock` `rule_set:<id>` (one waiting run per rule set: a
  trigger meeting a waiting run adds nothing, as that run will see the same mail).
- **The worker** (`mailroom/worker/`) runs nothing for an account whose Run filters is off, nor for
  a disabled rule set:
  - `run_rule_set` checks both again when it starts (else the run's error is `run_filters_off` or
    `rule_set_disabled`), commits `started_at`, runs the enabled filters (wrapped for starred only)
    through imap-thingy's `Folder.run_into` with no transaction open, and records `finished_at`,
    the report and the error (the filters finished so far kept). A pattern out of imap-thingy's
    match timeout is `match_timeout`, logged by its exception's type only (its text quotes the
    pattern); anything unexpected is `run_failed`, its exception's type name only.
    A run removed meanwhile (with its rule set or account) is left alone. A run whose job is
    neither waiting nor running when the worker starts (its worker stopped during it) is closed
    with the error `interrupted`.
  - `dispatch_scheduled`, a procrastinate periodic task every minute, queues each runnable
    `scheduled` rule set whose last scheduled run was queued at least `interval_minutes` ago
    (30 s early, for the minute's start), or never.
  - The **IDLE supervisor**, a thread of the worker process, keeps one imap-thingy `monitor` (on
    its own thread, stoppable) per (user, account, folder) of the runnable `new_mail` rule sets.
    Every 30 s it starts and stops watches to match the rule sets and Run filters, restarts one
    whose account's server, login or password changed, and starts again one that died (its first
    connection failed): at once, then after 30 s, doubling up to 30 min while it keeps failing
    without reaching its folder; each new reason is logged once. New mail, and every (re)connection of a watch
    (for mail that came while unwatched), queues the folder's `new_mail` runs; the IDLE
    connection itself never acts. Filter edits need no restart: each run loads them.
  - Jobs run 4 at a time (synchronous tasks, each on a thread); besides being notified of new jobs,
    the worker looks for them every second, so a run held back by its mailbox's lock starts within
    a second of the lock freeing. One worker process (below): a second would watch every folder
    twice, so it exits.
  - SIGTERM or SIGINT: procrastinate gives the jobs in progress 60 s, then the watches are stopped
    (each within the 20 s IMAP timeout).
- **A run's `result`** is imap-thingy's folder report, each filter's entry with its `filter_id` and
  the spec as stored; a dry run's adds `limited_to_newest` (null when the whole folder was looked
  at). A real run keeps the first 200 matched messages per filter, with `matched_total` when there
  were more; when it fails part way, `result` holds the filters finished before the failure and
  `error` repeats the failure. `error` is `<code>: <message>`.
- **Logs:** `rule_set_added`, `rule_set_changed`, `rule_set_removed`, `filter_added`,
  `filter_changed`, `filter_removed`, `filters_reordered`, `dry_run` (with `outcome`),
  `run_requested`; from the worker `run_queued` and `rule_set_run` (with `trigger` and `outcome`:
  `ok`, the error's code, or `gone`); with `user_id` and `rule_set_id`, `filter_id` or `run_id`,
  and from the API `via` as for accounts; never a spec or a message.

## Data persistence & schema migrations

- All app tables live in PostgreSQL schema **`app`** (`Base.metadata` schema). Alembic's version
  table is **`app.alembic_version`**, so mailroom-core's history (schema `core`) can share the
  database without either seeing the other's. `alembic/env.py` creates the schema before Alembic
  writes its version table; autogenerate only looks at `app`.
- **procrastinate**'s tables live in schema **`procrastinate`**, out of autogenerate. A revision
  applies procrastinate's own `schema.sql`, frozen beside it (`alembic/versions/sql/`); a later
  procrastinate release, whose minor `pyproject.toml` bounds, comes with a revision applying its
  migration files. The worker's connections put that schema on their `search_path`.
- mailroom-core's tables live in schema **`core`**, under its own Alembic history and version
  table (`core.alembic_version`). Everything that migrates (API startup, `mise run migrate`,
  `reset-db`, the test databases, `check-migrations`) runs mailroom-core's `migrate` first, then
  the app's history, so app revisions may rely on `core`.
- On the production host the database and role are **`mailroom`**; standalone mailroom-mcp uses **`mailroom_mcp`**.
- Revisions are **timestamped** (`YYYYMMDDHHMM`, file `<rev>_<slug>.py`) and form one linear chain.
- Every revision **downgrades**: `mise run check-migrations` runs upgrade → downgrade base →
  upgrade on a scratch database, locally and in CI.
- The API migrates at startup (`MAILROOM_RUN_STARTUP_MIGRATIONS=1` in the image and in dev), schema
  `core` first; run a
  single API worker, or migrate externally before scaling. The worker **never** migrates: it waits
  until the database is at its head, then starts.
- **One worker process** at a time: it holds a session advisory lock, and a second one exits. At
  start it marks failed every job still `doing`, which a stopped worker left (its `lock` would hold
  back every later job with the same lock). Stopping, it gives running jobs 60 s, then aborts them.
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
Locales live in `web/src/locales/<lng>/<namespace>.json`. Shared strings are in `common`; a feature
whose screens load lazily keeps its own namespace (`filters`), added when they load and falling
back to `common`, so its strings stay out of the first load.

## CI/CD and releases

[`ci-cd.md`](ci-cd.md).
