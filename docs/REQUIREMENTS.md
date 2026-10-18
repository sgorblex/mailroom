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

**Maturity: Partial** — sign-in and managing users are done; accounts are not started.

- **Users** sign in with a username and password (passkeys later). Every route except health,
  readiness, version, sign-in, sign-out and the signed-in check requires a
  [user session](GLOSSARY.md); the web app shows the sign-in form in place of any page until then.
  How: [`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md)
  — *Sign-in*.
- **Users are separate.** Each user's accounts, filters and suggestions are theirs alone; nothing
  is shared. An [admin](GLOSSARY.md) adds and removes users and resets their passwords on the
  *Users* page (the first admin comes from `mise run create-user <name> --admin`); there is no
  public sign-up. Every user changes their own password on their *Profile* page.
- **Accounts** (IMAP + SMTP, identities, send mode) come from **mailroom-core**'s model; the app
  implements the `AccountStore` integration point over its own schema, with secrets sealed as
  mailroom-core seals them (byte-compatible with mailroom-mcp, so its data imports unchanged).
- Account management (add, test, edit, remove) lives in the app's settings; the embedded MCP runs
  with its own settings page off.

## Mail client

**Maturity: Planned** — target is **between tier 2 and tier 3**: tiers 1 and 2 in full, on top of
the index and live updates; tier 3 items arrive one at a time.

| Tier | Scope |
| --- | --- |
| **1 — Read** | Accounts and folders with unread counts; message list (threaded or flat) with paging; read a message: headers, text and **sandboxed HTML** body, attachments (download), remote content blocked with a per-message reveal. Search. |
| **2 — Triage and compose** | Flag/unflag, read/unread, move, archive, trash (never expunge), multi-select. Compose new, reply, reply-all, forward, with identity (`From`) choice, attachments and drafts saved to the server's `\Drafts`. Sending obeys the account's **send mode** (mailroom-core's send gate). Keyboard shortcuts for triage. |
| **2+ — Index and live updates** | A **local index** of every account and folder (metadata plus rendered text) for fast cross-account search and lists; kept fresh by the worker over **IMAP IDLE** (and periodic resync), pushed to the browser (SSE or WebSocket). |
| **3 — Later** | Rich-text compose, signatures, snooze and send-later, contacts and autocomplete from history, calendar invites, offline reading, PGP/S/MIME, unified inbox views, saved searches. |

## Filters UI

**Maturity: Planned**

- Lists, edits, enables and disables **imap-thingy** rules through the library's own rule
  serialisation (imap-thingy's rule spec), per account.
- **Dry run** against real mail before saving: which messages a rule would match and what it would
  do.
- Rules run in the app's worker through **imap-thingy**'s runner — only for a mailbox the
  filterd daemon no longer acts on (never two runners on one mailbox).

## Filter suggestions (learning)

**Maturity: Planned**

- Observes each user's manual moves (and other triage) and proposes **filters** that would have done
  the same: the rule, the evidence (the messages it is based on), and a dry-run preview.
- **Suggestions only, never acting.** A suggestion does nothing until its user accepts it, which
  creates an ordinary filter they can edit; dismissing one is remembered. Nothing learned ever
  moves, flags, deletes or sends mail, or enables a filter, by itself.
- Learning runs locally; no mail leaves the host for it.

## MCP host

**Maturity: Planned** — mounts **mailroom-mcp**'s ASGI app at `/mcp/<profile>` in the API process,
with the app supplying `AccountStore`, `OwnerAuth` (consent and settings use the app's session)
and `AuditSink`. **Open:** those points assume one owner; each user getting their own MCP needs
mailroom-mcp's integration points to carry the user. The MCP's own schema (`mcp`) and Alembic history stay its own. Standalone
mailroom-mcp keeps working without the app.

## Non-functional requirements

[`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) — stack,
security (HTML mail sandbox, CSP, remote content), migrations, logging, testing, i18n.
[`requirements/ci-cd.md`](requirements/ci-cd.md) — Git workflow, CI gates, versioning.

## Out of scope

See [CONTEXT.md](CONTEXT.md) — *Non-goals*.
