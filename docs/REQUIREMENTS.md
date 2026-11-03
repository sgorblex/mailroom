# Requirements

**Documentation index:** [README.md](README.md). Product context and the component family:
[CONTEXT.md](CONTEXT.md).

Each capability carries a **maturity**:

| Maturity | Meaning |
| --- | --- |
| **Planned** | Agreed direction; not yet specified enough to build against. |
| **Current** | In scope now: defined enough to build against; acceptance evolves with development. |
| **Partial** | Some behaviour exists; gaps are listed inline. |
| **Done** | Meets the current slice (still revisable when priorities shift). |

---

## Platform skeleton

**Maturity: Done** — FastAPI API with `GET /api/v1/health` (liveness for the SPA), `/healthz`
(container healthcheck), `/api/v1/ready` (database reachable) and `/api/v1/version`; Alembic in
schema `app` with its own version table; the web app's placeholder screen shows the health check
through the Vite proxy. Engineering baseline:
[`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md).

## Users and accounts

**Maturity: Done** — sign-in, managing users and each user's mail accounts.

- **Users** sign in with a username and password (passkeys later). Every route except health,
  readiness, version, sign-in, sign-out and the signed-in check requires a
  [user session](GLOSSARY.md); the web app shows the sign-in form in place of any page until then.
  How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Sign-in*.
- **Users are separate.** Each user's accounts, filters and suggestions are theirs alone; nothing
  is shared. An [admin](GLOSSARY.md) adds and removes users and resets their passwords on the
  *Users* page (the first admin comes from `mise run create-user <name> --admin`); there is no
  public sign-up. Every user changes their own password on their *Profile* page.
- **Single sign-on** (optional): "Sign in with …" an OpenID Connect provider, for users an admin
  already created; nobody signs up through it. A user links the provider's sign-in on their
  *Profile* page (and unlinks it there), or, when the instance says so, their first SSO sign-in
  links by username. How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Single sign-on*.
- **Accounts** (IMAP + SMTP, identities, send mode) come from **mailroom-core**'s model; the app
  keeps them in mailroom-core's own schema (`core`, its `AccountStore`) in the app's database, with secrets sealed as
  mailroom-core seals them (byte-compatible with mailroom-mcp, so its data imports unchanged).
- Each user manages their own accounts on the *Accounts* page: add, edit (the id stays; an empty
  password keeps the stored one), test the IMAP login, remove (after a confirmation; the mail on
  the server stays), and a per-account **Run filters** switch, off until turned on. mailroom-core's
  config models check every change, so a refusal names the field. Removing a user removes their
  accounts, stored passwords and send log with them.
  How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Mail accounts*.

## Mail client

**Maturity: Current** for slice A (tier 1, read), specified in
[`requirements/mail-client.md`](requirements/mail-client.md); **Planned** for the rest. Target is
**between tier 2 and tier 3**: tiers 1 and 2 in full, on top of
the index and live updates; tier 3 items arrive one at a time.

| Tier | Scope |
| --- | --- |
| **1 — Read** | Accounts and folders with unread counts; message list (threaded or flat) with paging; read a message: headers, text and **sandboxed HTML** body, attachments (download), remote content blocked with a per-message reveal. Search. |
| **2 — Triage and compose** | Flag/unflag, read/unread, move, archive, trash (never expunge), multi-select. Compose new, reply, reply-all, forward, with identity (`From`) choice, attachments and drafts saved to the server's `\Drafts`. Sending obeys the account's **send mode** (mailroom-core's send gate). Keyboard shortcuts for triage. |
| **2+ — Index and live updates** | A **local index** of every account and folder (metadata plus rendered text) for fast cross-account search and lists; kept fresh by the worker over **IMAP IDLE** (and periodic resync), pushed to the browser (SSE or WebSocket). |
| **3 — Later** | Rich-text compose, signatures, snooze and send-later, contacts and autocomplete from history, calendar invites, offline reading, PGP/S/MIME, unified inbox views, saved searches. |

## Filters UI

**Maturity: Done** — as specified in [`requirements/filters.md`](requirements/filters.md).

- Per account, **rule sets** of **imap-thingy** filters, one folder each, stored through the
  library's own rule serialisation, triggered on new mail, on a schedule or by hand, optionally
  for starred mail only.
- **Dry run** against real mail before enabling: which messages each filter would match and what it
  would do.
- The worker runs them through **imap-thingy**'s runner, only for accounts whose user turned
  **Run filters** on (off by default), one run at a time per account; each run is kept in
  the rule set's history.

## Filter suggestions (learning)

**Maturity: Planned**

- Observes each user's manual moves (and other triage) and proposes **filters** that would have done
  the same: the rule, the evidence (the messages it is based on), and a dry-run preview.
- **Suggestions only, never acting.** A suggestion does nothing until its user accepts it, which
  creates an ordinary filter they can edit; dismissing one is remembered. Nothing learned ever
  moves, flags, deletes or sends mail, or enables a filter, by itself.
- Learning runs locally; no mail leaves the host for it.

## MCP server

**Maturity: Partial** — as specified in [`requirements/mcp.md`](requirements/mcp.md): `/mcp`,
the `filters_*` tools, the built-in authorization server with its `/connect` consent screen,
connected apps on the profile and external issuer mode are in.

- The app's own MCP server at `/mcp`, for each user's own data: accounts, folders, rule sets,
  dry runs and runs, in [tool groups](GLOSSARY.md) chosen by instance config. Mail tools come
  group by group ([`requirements/mcp.md`](requirements/mcp.md)), `mail_read` with the first
  slice of the [mail client](requirements/mail-client.md).
- **Nothing turns on without the user's say:** a rule set created over MCP starts disabled, an
  enabled one cannot be edited, and enabling, *Run now* and the *Run filters* switch are separate
  tools marked destructive, so the client asks first.
- **OAuth:** a built-in authorization server by default (sign in with a Mailroom password or SSO,
  then approve the client), or tokens from an external issuer such as Authentik; each approval is
  a [connected app](GLOSSARY.md) the user can revoke.
- **Web SSO** over OIDC for users an admin created, through a [linked sign-in](GLOSSARY.md);
  nobody signs up through it.

## Non-functional requirements

[`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) — stack,
security (HTML mail sandbox, CSP, remote content), migrations, logging, testing, i18n.
[`requirements/ci-cd.md`](requirements/ci-cd.md) — Git workflow, CI gates, versioning.

## Out of scope

See [CONTEXT.md](CONTEXT.md) — *Non-goals*.
