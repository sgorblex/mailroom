# Product context

## What Mailroom is

Mail for a few people, each kept separate, on one instance they run themselves: a **web mail
client** over each user's IMAP/SMTP accounts, a **filters UI** for the rules that sort that mail on the server, its **own MCP
server** that lets Claude work with it, and — later — **suggestions** for new filters learned from
how each user files mail by hand. The filters UI comes first; the client follows.

## Audience

**A few users on one instance.** Each has their own login, and their own mail accounts, filters
and suggestions, which nobody else sees. An admin creates the users; there is no public sign-up.
Nothing is shared between users.

## The component family

Mailroom the app is the top of a small family, and **dependencies only point downwards**:

```
Mailroom app (this repo)  web client, filters UI, own MCP server at /mcp (OAuth, SSO)
  ├─ mailroom-core        accounts, sealed secrets, IMAP/SMTP, send gate,  (backend/core,
  │                       rendering, audit                                  workspace package)
  └─ imap-thingy          IMAP filter library and runner                   (PyPI)

mailroom-mcp              standalone MCP server for mail (repo mailroom-mcp, being retired)
```

- **The app has its own MCP server** (`mailroom-app`, [`requirements/mcp.md`](requirements/mcp.md)),
  written in the app, over the app's own services, for each user's own data. It starts with
  filters (rule sets, dry runs, runs), which mailroom-mcp lacks, plus the accounts and folders
  listing they need. It writes its own mail tools over mailroom-core, turning them on group by
  group ([`requirements/mcp.md`](requirements/mcp.md)), `mail_read` first, `mail_draft` and
  `mail_send` with the compose slice.
- **mailroom-mcp is to be retired** (decided on 2026-11-09). It keeps serving mail until the
  app's MCP covers what it is used for, then its deployment stops and its repository is archived.
  The app does not embed or import it. mailroom-core started there, as the Python port's
  shared layer, and accounts in schema `core` are byte-compatible, so mailroom-mcp's data imports
  unchanged.
- **The app's built-in OAuth authorization server is self-contained** (the database and a small
  user interface, no app routes), so it may move to a shared package later.
- **mailroom-core** is the mail layer: `render`, `mail` (connections in `mail.imap`, read,
  organize, compose, drafts) and `send` (SMTP and the send gate), which the mail client and the
  MCP mail tools are built on. It lived in mailroom-mcp until 0.1.3, its last PyPI release, and
  is now a workspace package in `backend/core`, with its history, released only as part of the
  app. It never imports `mailroom`. The app keeps the accounts
  (`AccountStore`) in mailroom-core's schema `core` in the app's database, under mailroom-core's own
  Alembic history (`core.alembic_version`); the app's tables live in schema `app`.
- **imap-thingy** stays a general-purpose library on PyPI; the app reads and writes its rules
  through the library's own serialisation, never a private format, and runs them with its runner
  (`imap_thingy.runner`: run, IDLE monitor, starred-only).
- **No component imports the app**, and none learns about it. A need that seems to require that is a
  design question, answered by an integration point in the lower component.

The backend depends on **imap-thingy** from PyPI and on **mailroom-core** as a uv workspace
member (`backend/pyproject.toml` is the workspace root). For a change that spans repositories,
`mise run deps-local` installs a local editable imap-thingy in its place (the main checkout's
sibling, or `IMAP_THINGY_PATH`) without touching a tracked file, and `mise run deps-pinned`
switches back. mailroom-core's pytest plugin (no-network guard, mail and store fixtures) loads in
the backend's tests; core's own tests run separately, from `backend/core` (`mise run test-core`).

### Where the family is heading

Decided on 2026-11-09, in this order ([`TODO.md`](TODO.md) — *Family roadmap*):

- **mailroom-core folds into this repository** as a workspace package, since the app is now its
  only consumer. That ends the step of releasing core and then bumping the app's pin. Done:
  `backend/core`.
- **Editions:** an instance chooses its features in config. There is always a base: users,
  accounts and their secrets, sign-in, a settings UI with `/connect`, and Postgres. On top of it,
  the mail client, the MCP server and filters are each optional. A disabled feature runs nothing:
  no routes, worker or screens. Every edition uses Postgres.
- **This repository goes public** once the app is in a decent state, after an audit of its whole
  history. **imap-thingy folds in then, not before**, because it is public already. It keeps its
  history and its PyPI name, and it never depends on mailroom-core.
- **Command-line tools:**
  - a client for a running instance, after personal API tokens exist;
  - `imap-thingy run`, for filters with no server at all.

## Client scope

Between **tier 2** and **tier 3** of [`REQUIREMENTS.md`](REQUIREMENTS.md) — *Mail client*: reading
and triage plus composing (plain text, with the send mode limiting what agents send), over a **local index** with **live updates** (IMAP IDLE), first. Tier 3
features come later, one by one, when they earn their place.

## Reversed non-goals

mailroom-mcp's design ruled out **a webmail UI** and **filter management**. Mailroom is where both
now live — deliberately outside mailroom-mcp, which is to be retired (*The component family*). Filter management reaches models through the app's own MCP server instead.

## Non-goals (unless requirements say otherwise)

- Sharing between users, delegation, or multi-tenancy across instances. Users are separate.
- A mail server: Mailroom is a client of existing IMAP/SMTP servers. No Sieve management (server-side
  rules stay with imap-thingy).
- POP3, Exchange/EWS, Microsoft Graph.
- **Autonomous action.** Nothing Mailroom learns ever moves, flags, deletes or sends mail by itself;
  it suggests, and the user decides.
- Marketing, tracking or bulk-mail features.

## Security posture

Mail is **hostile input**. HTML mail renders only in a **sandboxed iframe** under a **strict CSP**,
with **no remote content by default**; the app itself is private, and each user sees only their own mail. Detail:
[`requirements/nonfunctional-requirements.md`](requirements/nonfunctional-requirements.md) —
*Security*.
